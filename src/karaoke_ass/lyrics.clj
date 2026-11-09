(ns karaoke-ass.lyrics
  (:require [clojure.data.json :as json]
            [clojure.string :as str]
            [karaoke-ass.util :as u])
  (:import [java.net URI URLEncoder HttpURLConnection]
           [java.text Normalizer Normalizer$Form]))

(defn comparison [s]
  (str/replace (u/lower (Normalizer/normalize s Normalizer$Form/NFKC)) #"[^\p{L}\p{N}]" ""))
(defn metadata-key [s]
  (str/join " " (str/split (str/trim (u/lower (Normalizer/normalize s Normalizer$Form/NFKC))) #"\s+")))
(defn fetch-record [artist title]
  (let [url (str "https://lrclib.net/api/get?artist_name=" (URLEncoder/encode artist "UTF-8")
                 "&track_name=" (URLEncoder/encode title "UTF-8"))
        connection ^HttpURLConnection (.openConnection (.toURL (URI/create url)))]
    (try
      (.setConnectTimeout connection 10000) (.setReadTimeout connection 10000)
      (.setRequestProperty connection "User-Agent" (str "karaoke-ass/" u/version))
      (.setRequestProperty connection "Accept" "application/json")
      (with-open [in (.getInputStream connection)]
        (let [payload (.readNBytes in 2000001)]
          (when (> (alength payload) 2000000) (u/fail "Respuesta de letras demasiado grande."))
          (json/read-str (String. payload java.nio.charset.StandardCharsets/UTF_8) :key-fn keyword)))
      (finally (.disconnect connection)))))
(defn fetch-lyrics [artist title]
  (let [record (fetch-record artist title)]
    (when-not (and (map? record) (string? (:artistName record)) (string? (:trackName record))
                   (= (metadata-key artist) (metadata-key (:artistName record)))
                   (= (metadata-key title) (metadata-key (:trackName record))))
      (u/fail "Artista o título de la letra no coincide."))
    (when (:instrumental record) (u/fail "El registro está marcado como instrumental."))
    (let [plain (:plainLyrics record) synced (:syncedLyrics record)
          text (if (and (string? plain) (not (str/blank? plain))) plain
                   (when (string? synced)
                     (str/join "\n" (for [line (str/split-lines synced) :when (re-find #"^\s*\[\d+:\d+(?:\.\d+)?\]" line)]
                                      (str/trim (str/replace line #"\[\d+:\d+(?:\.\d+)?\]" ""))))))
          id (:id record)]
      (when (str/blank? text) (u/fail "No hay letra disponible."))
      (when-not (and (integer? id) (not (neg? id))) (u/fail "Identificador de letra inválido."))
      [text {:provider "LRCLIB" :id id :url (str "https://lrclib.net/api/get/" id)}])))

(defn longest-match [a b alo ahi blo bhi]
  (loop [i alo previous {} best [alo blo 0]]
    (if (>= i ahi) best
      (let [[current best]
            (reduce (fn [[row [bi bj bn :as best]] j]
                      (if (= (nth a i) (nth b j))
                        (let [n (inc (get previous (dec j) 0))]
                          [(assoc row j n) (if (> n bn) [(inc (- i n)) (inc (- j n)) n] best)]) [row best]))
                    [{} best] (range blo bhi))]
        (recur (inc i) current best)))))
(defn matching-blocks [a b]
  (loop [queue [[0 (count a) 0 (count b)]] matches []]
    (if-let [[alo ahi blo bhi] (peek queue)]
      (let [[i j n :as match] (longest-match a b alo ahi blo bhi)]
        (recur (cond-> (pop queue)
                 (and (pos? n) (< alo i) (< blo j)) (conj [alo i blo j])
                 (and (pos? n) (< (+ i n) ahi) (< (+ j n) bhi)) (conj [(+ i n) ahi (+ j n) bhi]))
               (cond-> matches (pos? n) (conj match))))
      (let [collapsed (reduce (fn [out [i j n :as match]]
                                (let [[pi pj pn] (peek out)]
                                  (if (and pi (= i (+ pi pn)) (= j (+ pj pn)))
                                    (conj (pop out) [pi pj (+ pn n)]) (conj out match)))) [] (sort matches))]
        (conj collapsed [(count a) (count b) 0])))))
(defn opcodes [a b]
  (loop [i 0 j 0 blocks (matching-blocks a b) out []]
    (if-let [[ai bj n] (first blocks)]
      (let [tag (cond (and (< i ai) (< j bj)) "replace" (< i ai) "delete" (< j bj) "insert")]
        (recur (+ ai n) (+ bj n) (next blocks)
               (cond-> out tag (conj [tag i ai j bj]) (pos? n) (conj ["equal" ai (+ ai n) bj (+ bj n)])))) out)))
(defn similarity [a b]
  (if (zero? (+ (count a) (count b))) 1.0
    (/ (* 2.0 (reduce + (map #(nth % 2) (matching-blocks a b)))) (+ (count a) (count b)))))
(defn unique-pair? [sequence pair]
  (= 1 (count (filter #(= pair (vec %)) (partition 2 1 sequence)))))
(defn correct-words [words lyrics]
  (let [tokens (vec (filter #(seq (comparison %)) (str/split lyrics #"\s+")))
        source (mapv #(comparison (:text %)) words) target (mapv comparison tokens)
        operations (opcodes source target)]
    (reduce
      (fn [[result changes unresolved] [index [tag a b c d]]]
        (if (or (= tag "equal") (and (= tag "insert") (or (zero? a) (= a (count source)))))
          [result changes unresolved]
          (let [before (get operations (dec index)) after (get operations (inc index))
                eligible (and (= tag "replace") (= (- b a) (- d c) 1) (>= a 2) (<= (+ b 2) (count source))
                              (= "equal" (first before) (first after))
                              (>= (- (nth before 2) (nth before 1)) 2) (>= (- (nth after 2) (nth after 1)) 2)
                              (every? #(and (unique-pair? source %) (unique-pair? target %)) [(subvec source (- a 2) a) (subvec source b (+ b 2))])
                              (seq (nth source a)) (>= (similarity (nth source a) (nth target c)) 0.75))]
            (if eligible
              [(assoc-in result [a :text] (nth tokens c))
               (conj changes {:before (:text (nth words a)) :after (nth tokens c)
                              :start (/ (:start_cs (nth words a)) 100.0) :end (/ (:end_cs (nth words a)) 100.0)}) unresolved]
              (let [left (min a (dec (count words))) right (max left (dec b))]
                [result changes (conj unresolved {:start (/ (:start_cs (nth words left)) 100.0) :end (/ (:end_cs (nth words right)) 100.0)
                                                  :text (str/join " " (map :text (subvec words a b))) :reference (str/join " " (subvec tokens c d))})])))))
      [words [] []] (map-indexed vector operations))))
(defn apply-lyrics [words artist title issues]
  (let [report {:artist artist :title title :status "unavailable" :source nil :changes [] :unresolved []}
        fetched (try {:value (fetch-lyrics artist title)} (catch Exception e {:error (.getMessage e)}))]
    (if-let [reason (:error fetched)]
      (do (u/notice! issues "lyrics_unavailable" (/ (:start_cs (first words)) 100.0) (/ (:end_cs (peek words)) 100.0) "" (str "No se aplicaron correcciones de letra: " reason))
          [words (assoc report :reason reason)])
      (let [[text source] (:value fetched) [corrected changes unresolved] (correct-words words text)]
        (doseq [c changes] (u/notice! issues "lyrics_corrected" (:start c) (:end c) (:after c) (str "Letra de LRCLIB: " (:before c) " → " (:after c) ". Pendiente de revisión auditiva.")))
        (doseq [c unresolved] (u/notice! issues "lyrics_unresolved" (:start c) (:end c) (:text c) (str "Diferencia sin corregir con LRCLIB: " (:reference c))))
        [corrected (assoc report :status (if (seq changes) "corrected" "unchanged") :source source :changes changes :unresolved unresolved)]))))
