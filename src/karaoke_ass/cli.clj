(ns karaoke-ass.cli
  (:require [clojure.java.io :as io]
            [clojure.string :as str]
            [karaoke-ass.audio :as audio]
            [karaoke-ass.recognition :as recognition]
            [karaoke-ass.render :as render]
            [karaoke-ass.syllables :as syllables]
            [karaoke-ass.timing :as timing]
            [karaoke-ass.util :as u])
  (:import [java.io RandomAccessFile]
           [java.nio.channels OverlappingFileLockException]))

(def generated ["karaoke.ass" "transcripcion.original.json" "transcripcion.json" "transcripcion.txt"
                "unidades.tsv" "revision.json" "revision.txt" "ejecucion.json"])
(def defaults {:model "medium" :language "auto" :device "auto" :threads 4 :chunk-seconds 24.0 :prompt ""
               :start 0.0 :units "syllable" :effect "kf" :offset 0.0 :max-chars 72 :max-duration 6.0 :font "DejaVu Sans" :font-size 64
               :overwrite false :no-resume false :no-retry false})
(def option-types
  {:output :string :model :string :model-dir :string :language :string :device :string :threads :int :chunk-seconds :float
   :artist :string :title :string :prompt :string :start :float :end :float :from-json :string :syllables :string
   :units :string :effect :string :offset :float :max-chars :int :max-duration :float :font :string :font-size :int
   :overwrite :flag :no-resume :flag :no-retry :flag :help :flag :version :flag})
(def help-text
  (str "Pista vocal → karaoke ASS (Clojure). Tiempos silábicos estimados.\n\n"
       "Usage: karaoke-ass AUDIO [options]\n\n"
       (str/join "\n" (for [[key type] (sort-by (comp name first) option-types)]
                         (str "  --" (name key) (when-not (= type :flag) " VALUE")
                              (when (contains? defaults key) (str " (default: " (get defaults key) ")")))))
       "\n\n-o = --output; -h = --help. Whisper uses KARAOKE_PYTHON or .venv/bin/python.\n"
       "--from-json regenerates without Python/Whisper. See README.md for options and safeguards.\n"))
(defn parse-args [args]
  (loop [args (seq args) options defaults positional [] literal? false]
    (if-let [arg (first args)]
      (let [arg (case arg "-o" "--output" "-h" "--help" arg)]
        (cond
          (and (not literal?) (= arg "--")) (recur (next args) options positional true)
          (and (not literal?) (str/starts-with? arg "-"))
          (let [[flag inline] (str/split arg #"=" 2) key (keyword (str/replace-first flag #"^--" "")) type (get option-types key)]
            (when-not type (u/fail (str "Opción desconocida: " flag)))
            (if (= type :flag)
              (do (when inline (u/fail (str flag " no admite valor."))) (recur (next args) (assoc options key true) positional literal?))
              (let [value (or inline (second args))]
                (when-not value (u/fail (str "Falta valor para " flag)))
                (let [parsed (try (case type :int (Long/parseLong value) :float (Double/parseDouble value) value)
                                  (catch NumberFormatException _ (u/fail (str "Valor no válido para " flag ": " value))))]
                  (recur (if inline (next args) (nnext args)) (assoc options key parsed) positional literal?)))))
          :else (recur (next args) options (conj positional arg) literal?)))
      (if (or (:help options) (:version options)) options
        (do (when-not (= 1 (count positional)) (u/fail "Se requiere un archivo de audio. Usa --help."))
            (assoc options :audio (first positional)))))))

(defn resolve-path [path]
  (let [file (io/file path)]
    (.getCanonicalFile (if (and (not (.isAbsolute file)) (System/getenv "KARAOKE_WORKING_DIRECTORY"))
                         (io/file (System/getenv "KARAOKE_WORKING_DIRECTORY") path) file))))
(defn validate [options]
  (let [options (reduce (fn [o k] (if-let [p (get o k)] (assoc o k (resolve-path p)) o)) options [:audio :output :from-json :syllables :model-dir])
        options (if (.isFile (resolve-path (:model options))) (update options :model #(str (resolve-path %))) options)
        artist (:artist options) title (:title options)]
    (when (not= (some? artist) (some? title)) (u/fail "--artist y --title deben usarse juntos."))
    (when (and artist (or (str/blank? artist) (str/blank? title))) (u/fail "--artist y --title no pueden estar vacíos."))
    (when-not (.isFile (io/file (:audio options))) (u/fail (str "No existe el audio: " (:audio options))))
    (doseq [k [:chunk-seconds :start :offset :max-duration]]
      (when-not (u/finite? (get options k)) (u/fail (str (name k) " debe ser finito."))))
    (when (and (:end options) (or (not (u/finite? (:end options))) (<= (:end options) (:start options)))) (u/fail "--end debe ser finito y mayor que --start."))
    (when-not (and (>= (:start options) 0) (>= (:threads options) 1) (<= 5 (:chunk-seconds options) 24)
                   (<= 20 (:max-chars options) 120) (<= 0.5 (:max-duration options) 15) (<= 12 (:font-size options) 160))
      (u/fail "Revisa start ≥ 0, threads ≥ 1, chunk-seconds (5–24), max-chars (20–120), max-duration (0,5–15), font-size (12–160)."))
    (doseq [[key choices] [[:device #{"auto" "cpu" "cuda"}] [:units #{"word" "syllable"}] [:effect #{"k" "kf" "ko"}]]]
      (when-not (choices (get options key)) (u/fail (str "Valor no válido para --" (name key)))))
    (when (or (str/blank? (:font options)) (re-find #"[,\n\r{}\\]" (:font options))) (u/fail "Nombre de fuente no válido para ASS."))
    (when (and (str/ends-with? (:model options) ".en") (not (#{"auto" "en"} (:language options)))) (u/fail "Un modelo .en solo admite inglés."))
    (cond-> options artist (assoc :artist (str/trim artist) :title (str/trim title)))))

(defn with-output-lock [directory f]
  (with-open [file (RandomAccessFile. (io/file directory ".run.lock") "rw") channel (.getChannel file)]
    (let [lock (try (.tryLock channel) (catch OverlappingFileLockException _ nil))]
      (when-not lock (u/fail "Otra ejecución está usando la misma carpeta de salida."))
      (try (f) (finally (.release lock))))))

(defn run [input-options]
  (let [started (System/nanoTime) options (validate input-options)
        stem (str/replace (.getName (io/file (:audio options))) #"\.[^.]*$" "")
        output (or (:output options) (resolve-path (str "results/" stem)))
        options (assoc options :output output :overrides (if (:syllables options) (syllables/validate-overrides (u/read-json (:syllables options))) {}))]
    (.mkdirs (io/file output))
    (let [protected (set (map #(.getCanonicalPath (io/file output %)) (concat generated [".checkpoint.json" ".run.lock"])))
          inputs (remove nil? [(:audio options) (:from-json options) (:syllables options)])]
      (when (some #(protected (.getCanonicalPath (io/file %))) inputs) (u/fail "La carpeta de salida contiene un archivo usado como entrada; usa otra carpeta.")))
    (with-output-lock output
      (fn []
        (when (and (not (:overwrite options)) (some #(.exists (io/file output %)) generated))
          (u/fail (str "Ya existen resultados en " output ". Usa otra carpeta o --overwrite.")))
        (u/log "Leyendo audio…")
        (let [samples (audio/load-audio (:audio options)) duration (/ (alength ^floats samples) (double audio/rate))
              _ (when (>= (:start options) duration) (u/fail "--start está fuera del audio."))
              energy (audio/envelope samples) mask (audio/activity energy) source-hash (u/file-hash (:audio options))
              original (if (:from-json options)
                         (let [data (u/read-json (:from-json options))]
                           (when-not (map? data) (u/fail "La transcripción debe ser un objeto JSON."))
                           (when (and (:source_sha256 data) (not= source-hash (:source_sha256 data))) (u/fail "SHA-256 distinto: la transcripción corresponde a otro audio."))
                           (assoc data :source_sha256 source-hash))
                         (recognition/recognize samples mask options (io/file output ".checkpoint.json") source-hash))
              interval-end (min duration (or (:end options) duration))
              raw (filterv #(<= (:start options) (/ (+ (:start %) (:end %)) 2.0) interval-end)
                           (recognition/words-from-json original (if (= "auto" (:language options)) "es" (:language options))))
              issues (atom [])
              _ (doseq [w (:windows original) :when (< (:coverage w 1) 0.65)]
                  (u/notice! issues "low_window_coverage" (:start w) (:end w) "" "El texto cubre poca señal activa; revisar posibles omisiones."))
              raw (if (:from-json options) raw
                    (filterv (fn [w]
                               (let [a (max 0 (long (* (:start w) 100))) b (min (count mask) (long (Math/ceil (* (:end w) 100))))]
                                 (if (and (> b a) (not-any? true? (subvec mask a b)))
                                   (do (u/notice! issues "silent_word" (:start w) (:end w) (:text w) "Texto producido en silencio eliminado; se conserva en el JSON original.") false) true))) raw))
              words (timing/normalize-words raw duration issues)]
          (when (empty? words)
            (u/atomic-json (io/file output "transcripcion.original.json") original)
            (u/fail "No se reconocieron palabras con tiempos utilizables; no se generó un ASS vacío."))
          (let [original (cond-> original (or (pos? (:start options)) (:end options)) (assoc :interval [(:start options) interval-end]))
                report (render/write-outputs output words original energy mask issues options duration)
                manifest (cond-> {:version u/version :implementation "clojure" :source (str (:audio options)) :source_sha256 source-hash :duration duration
                                  :elapsed_seconds (/ (u/rounded (/ (- (System/nanoTime) started) 10000000.0)) 100.0)
                                  :model (:model original) :from_json (some-> (:from-json options) str) :recognition_device (:device original)
                                  :cuda_oom_fallback (:cuda_oom_fallback original false)
                                  :options (into {} (for [[k v] (dissoc options :overrides)] [(keyword (str/replace (name k) "-" "_")) (if (instance? java.io.File v) (str v) v)]))
                                  :lines (:lines report) :units (:units report) :review_issues (count @issues) :status "draft_requires_review"}
                           (:lyrics report) (assoc :lyrics (:lyrics report)))]
            (u/atomic-json (io/file output "ejecucion.json") manifest)
            (println (str "ASS generado: " (io/file output "karaoke.ass")))
            (println (format "%d líneas, %d unidades, %d avisos. Cortes silábicos estimados." (:lines report) (:units report) (count @issues)))))))
    0))
(defn main [args]
  (try
    (let [options (parse-args args)]
      (cond (:help options) (do (print help-text) 0) (:version options) (do (println u/version) 0) :else (run options)))
    (catch InterruptedException _ (u/log "Interrumpido. Repite el comando para reanudar los tramos guardados.") 130)
    (catch Exception e (u/log (str "Error: " (.getMessage e))) 2)))
(defn -main [& args]
  (let [status (main args)] (shutdown-agents) (System/exit status)))
