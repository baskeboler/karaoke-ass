(ns karaoke-ass.render
  (:require [clojure.java.io :as io]
            [clojure.string :as str]
            [karaoke-ass.lyrics :as lyrics]
            [karaoke-ass.timing :as timing]
            [karaoke-ass.util :as u]))

(defn timestamp [cs]
  (format "%d:%02d:%02d.%02d" (quot cs 360000) (quot (mod cs 360000) 6000) (quot (mod cs 6000) 100) (mod cs 100)))
(defn safe-text [text]
  (str/join " " (str/split (str/trim (str/escape text {\\ "＼" \{ "｛" \} "｝"})) #"\s+")))
(defn separator [previous current]
  (if (or (empty? previous) (re-find #"^[.,;:!?\-'’)\]]" current)) "" " "))
(defn plain-text [words]
  (reduce (fn [text w] (str text (separator text (:text w)) (:text w))) "" words))
(defn group-lines [words max-chars max-duration]
  (let [[groups current]
        (reduce (fn [[groups current] w]
                  (let [break? (and (seq current)
                                    (or (> (- (:end_cs w) (:start_cs (first current))) (* max-duration 100))
                                        (> (count (plain-text (conj current w))) max-chars)
                                        (> (- (:start_cs w) (:end_cs (peek current))) 100)))
                        groups (if break? (conj groups current) groups) current (conj (if break? [] current) w)]
                    (if (and (>= (count (plain-text current)) 18) (re-find #"[.!?,]$" (:text w)))
                      [(conj groups current) []] [groups current]))) [[] []] words)]
    (cond-> groups (seq current) (conj current))))
(defn shifted-words [words offset]
  (let [delta (u/rounded (* offset 100))]
    (vec (keep (fn [w]
                 (let [units (vec (for [unit (:units w) :when (pos? (+ (:end_cs unit) delta))]
                                    (assoc unit :source_start_cs (:start_cs unit) :source_end_cs (:end_cs unit)
                                                :start_cs (max 0 (+ (:start_cs unit) delta)) :end_cs (+ (:end_cs unit) delta))))]
                   (when (seq units) (assoc w :units units :start_cs (:start_cs (first units)) :end_cs (:end_cs (peek units)))))) words))))

(defn header [options]
  (str "[Script Info]\nTitle: Karaoke automático - revisar informe\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
       "; Tiempos silábicos estimados. Ver revision.json y revision.txt.\n"
       (format "; Desplazamiento de salida: %+.2f segundos.\n" (double (:offset options)))
       "; Vista previa del texto: 200 ms antes del canto, limitada al inicio del audio.\n\n"
       "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
       "Style: Default," (:font options) "," (:font-size options) ",&H0000FFFF,&H00FFFFFF,&H00181818,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,110,110,85,1\n\n"
       "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"))
(defn render [words options]
  (let [groups (group-lines (shifted-words words (:offset options)) (:max-chars options) (:max-duration options))
        rendered
        (map-indexed
          (fn [line-index group]
            (let [singing-start (:start_cs (first group)) end (:end_cs (peek group)) start (max 0 (- singing-start 20))
                  line-text (plain-text group)
                  break-at (when (and (> (count line-text) 42) (> (count group) 1))
                             (first (sort-by #(Math/abs (long (- (count (plain-text (subvec group 0 %))) (count (plain-text (subvec group %)))))) (range 1 (count group)))))
                  [_ text _ rows]
                  (reduce (fn [[cursor text previous rows] [wi word]]
                            (let [text (str text (when (> (:start_cs word) cursor) (str "{\\k" (- (:start_cs word) cursor) "}")))
                                  prefix (if (= wi break-at) "\\N" (separator previous (:text word)))
                                  text (str text (apply str (map-indexed (fn [ui unit]
                                                                                        (str "{\\" (:effect options) (- (:end_cs unit) (:start_cs unit)) "}" (when (zero? ui) prefix) (safe-text (:text unit)))) (:units word))))
                                  more (map-indexed (fn [ui unit]
                                                      {:linea (inc line-index) :inicio_s (/ (:start_cs unit) 100.0) :fin_s (/ (:end_cs unit) 100.0)
                                                       :inicio_audio_s (/ (:source_start_cs unit) 100.0) :fin_audio_s (/ (:source_end_cs unit) 100.0)
                                                       :palabra (:text word) :unidad (inc ui) :texto (:text unit) :idioma (:language word)
                                                       :metodo (if (= (:units options) "word") "limite_palabra" "silaba_estimada_energia")}) (:units word))]
                              [(:end_cs word) text (:text word) (into rows more)]))
                          [start "" "" []] (map-indexed vector group))
                  sum (reduce + (map #(Long/parseLong (second %)) (re-seq #"\\k(?:f|o)?(\d+)" text)))]
              (when-not (= sum (- end start)) (u/fail "Error interno: las unidades no suman la duración del evento."))
              {:event (str "Dialogue: 0," (timestamp start) "," (timestamp end) ",Default,,0,0,0,," text)
               :rows rows :transcript (str "[" (timestamp singing-start) " --> " (timestamp end) "] " line-text)})) groups)]
    [(str (header options) (str/join "\n" (map :event rendered)) "\n") (vec (mapcat :rows rendered)) (mapv :transcript rendered)]))

(def tsv-fields [:linea :inicio_s :fin_s :inicio_audio_s :fin_audio_s :palabra :unidad :texto :idioma :metodo])
(defn tsv-cell [x]
  (let [s (str x)] (if (re-find #"[\t\r\n\"]" s) (str "\"" (str/replace s "\"" "\"\"") "\"") s)))
(defn write-outputs [directory words original energy mask issues options duration]
  (let [path #(io/file directory %)
        normalized {:schema_version 1 :timeline "source_audio_seconds" :duration duration :source_sha256 (:source_sha256 original)
                    :interval (:interval original [0 duration])
                    :words (mapv #(-> % (dissoc :start_cs :end_cs) (assoc :start (/ (:start_cs %) 100.0) :end (/ (:end_cs %) 100.0))) words)}]
    (u/atomic-json (path "transcripcion.original.json") original)
    (u/atomic-json (path "transcripcion.json") normalized)
    (let [lyrics-report
          (when (:artist options)
            (let [[corrected report] (lyrics/apply-lyrics words (:artist options) (:title options) issues)]
              (u/atomic-json (path "transcripcion.json") (assoc normalized :words (mapv (fn [word correction] (assoc word :text (:text correction))) (:words normalized) corrected)))
              report))
          saved (u/read-json (path "transcripcion.json"))
          words (mapv #(assoc % :start_cs (u/rounded (* (:start %) 100)) :end_cs (u/rounded (* (:end %) 100))) (:words saved))
          units (timing/timed-units words energy (:overrides options) (:units options) issues)
          _ (apply timing/coverage-issues! words mask issues (:interval saved))
          [ass rows transcript] (render units options)]
      (when (empty? rows) (u/fail "No quedan subtítulos visibles después del desplazamiento."))
      (spit (path "karaoke.ass") (str "\uFEFF" ass) :encoding "UTF-8")
      (spit (path "transcripcion.txt") (str (str/join "\n" transcript) "\n") :encoding "UTF-8")
      (spit (path "unidades.tsv") (str (str/join "\t" (map name tsv-fields)) "\r\n"
                                     (str/join "\r\n" (for [row rows] (str/join "\t" (map #(tsv-cell (get row %)) tsv-fields)))) "\r\n") :encoding "UTF-8")
      (let [report (cond-> {:syllable_timing "estimated" :verified_by_listening false :offset_seconds (:offset options)
                           :preview_seconds 0.2 :issue_timeline "source_audio_seconds" :lines (count transcript) :units (count rows) :issues @issues}
                     lyrics-report (assoc :lyrics lyrics-report))
            summary (concat ["BORRADOR AUTOMÁTICO DE KARAOKE" "" "Los cortes entre sílabas son estimaciones, no una alineación fonética verificada."
                             "La división ortográfica usa reglas aproximadas para español e inglés." "Puede haber errores de texto, voces omitidas, sinalefas y melismas sin representar."
                             (format "Desplazamiento del ASS: %+.2f s. Los avisos usan tiempos del audio original." (double (:offset options)))
                             (format "%d líneas; %d unidades; %d avisos." (count transcript) (count rows) (count @issues)) ""]
                            (when lyrics-report [(str "Letra LRCLIB: " (:status lyrics-report) "; " (count (:changes lyrics-report)) " correcciones.")])
                            (when-let [url (get-in lyrics-report [:source :url])] [(str "Fuente: " url)])
                            (for [i @issues] (format "[%.2f–%.2f] %s: %s — %s" (double (:start i)) (double (:end i)) (:code i) (:text i) (:message i))))]
        (u/atomic-json (path "revision.json") report)
        (spit (path "revision.txt") (str (str/join "\n" summary) "\n") :encoding "UTF-8") report))))
