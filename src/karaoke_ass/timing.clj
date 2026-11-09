(ns karaoke-ass.timing
  (:require [clojure.string :as str]
            [karaoke-ass.syllables :as syllables]
            [karaoke-ass.util :as u]))

(defn normalize-words [words duration issues]
  (let [usable
        (vec (keep (fn [w]
                     (cond
                       (:missing_segment w) (do (u/notice! issues "segment_without_words" (:start w) (:end w) "" "Segmento sin tiempos de palabra.") nil)
                       (str/blank? (:text w)) nil
                       :else (do
                               (when (< (:end w) (:start w)) (u/fail (str "Tiempos invertidos para " (:text w))))
                               (when (or (neg? (:start w)) (> (:end w) duration))
                                 (u/notice! issues "clipped" (:start w) (:end w) (:text w) "Palabra recortada a la duración del audio."))
                               (assoc w :start_cs (max 0 (min (u/rounded (* duration 100)) (u/rounded (* (:start w) 100))))
                                        :end_cs (max 0 (min (u/rounded (* duration 100)) (u/rounded (* (:end w) 100)))))))) words))]
    (when (some (fn [[a b]] (> (:start_cs a) (:start_cs b))) (partition 2 1 usable))
      (u/notice! issues "reordered" 0 duration "" "Se ordenaron palabras cuyos tiempos no eran crecientes."))
    (reduce
      (fn [out w]
        (let [a (:start_cs w) b (:end_cs w) prev (peek out)
              overlap (when prev (- (min b (:end_cs prev)) (max a (:start_cs prev))))
              same (and prev (= (syllables/key-text (str/replace (:text w) #"[^\p{L}\p{N}_]" ""))
                                (syllables/key-text (str/replace (:text prev) #"[^\p{L}\p{N}_]" ""))))]
          (cond
            (and prev same (pos? overlap) (>= overlap (* 0.5 (max 1 (min (- b a) (- (:end_cs prev) (:start_cs prev)))))))
            (do (u/notice! issues "duplicate" (/ a 100.0) (/ b 100.0) (:text w) "Duplicado con tiempos superpuestos eliminado.") out)
            (and prev (<= b a) (> (- b (:end_cs prev)) 20))
            (do (u/notice! issues "unplaceable" (/ a 100.0) (/ b 100.0) (:text w) "Palabra de duración cero tras un silencio; se conserva en el JSON original.") out)
            :else
            (let [[out a] (if (and prev (or (< a (:end_cs prev)) (<= b a)))
                            (let [boundary (if (or (<= b (:end_cs prev)) (<= b a))
                                             (min (dec b) (max (inc (:start_cs prev)) (- b (min 25 (max 1 (quot (- b (:start_cs prev)) 3))))))
                                             (min a b))
                                  boundary (max (inc (:start_cs prev)) boundary)]
                              (if (>= boundary b) [out b]
                                (do (u/notice! issues "timing_repaired" (/ boundary 100.0) (/ b 100.0) (:text w) "Límite estimado al reparar solapamiento o duración cero.")
                                    [(conj (pop out) (assoc prev :end_cs boundary)) (if (< a b) (max a boundary) boundary)])))
                            [out a])]
              (if (<= b a)
                (do (u/notice! issues "unplaceable" (/ a 100.0) (/ b 100.0) (:text w) "Palabra sin espacio temporal; consultar transcripcion.original.json.") out)
                (do
                  (when (and (:probability w) (< (:probability w) 0.5))
                    (u/notice! issues "low_confidence" (/ a 100.0) (/ b 100.0) (:text w) "Confianza inferior a 0,5; no garantiza la letra."))
                  (when (> (- b a) 200)
                    (u/notice! issues "long_word" (/ a 100.0) (/ b 100.0) (:text w) "Palabra de más de 2 s; revisar vocal sostenida, omisiones o tiempos."))
                  (conj out (assoc w :start_cs a :end_cs b)))))))) [] (sort-by (juxt :start_cs :end_cs) usable))))

(defn boundaries [start end n energy]
  (when (< (- end start) n) (u/fail "No hay suficientes centésimas para las unidades solicitadas."))
  (conj (reduce (fn [cuts i]
                 (let [target (+ start (/ (* (- end start) i) (double n)))
                       radius (max 1.0 (* (/ (- end start) (double n)) 0.25))
                       lo (max (inc (peek cuts)) (long (- target radius)))
                       hi (min (- end (- n i)) (long (+ target radius)))
                       grid (range lo (inc hi))
                       value #(nth energy (max 0 (min (dec (count energy)) %)))
                       maximum (if (seq grid) (reduce max (map value grid)) 0)
                       score #(+ (/ (value %) (+ maximum 1.0e-12)) (* 0.35 (Math/pow (/ (- % target) radius) 2)))
                       cut (if (seq grid) (first (sort-by score grid))
                               (max (inc (peek cuts)) (min (- end (- n i)) (u/rounded target))))]
                   (conj cuts cut))) [start] (range 1 n)) end))

(defn timed-units [words energy overrides mode issues]
  (mapv (fn [word]
          (let [language (:language word "es")
                parts (if (= mode "word") [(:text word)] (syllables/split-word (:text word) language overrides))
                _ (when (and (= mode "syllable") (not (#{"es" "en"} language)))
                    (u/notice! issues "unsupported_syllables" (/ (:start_cs word) 100.0) (/ (:end_cs word) 100.0) (:text word) (str "Idioma " language ": se conserva la palabra entera.")))
                parts (if (> (count parts) (- (:end_cs word) (:start_cs word)))
                        (do (u/notice! issues "too_short" (/ (:start_cs word) 100.0) (/ (:end_cs word) 100.0) (:text word) "Palabra demasiado breve para separar sílabas.") [(:text word)]) parts)
                cuts (boundaries (:start_cs word) (:end_cs word) (count parts) energy)]
            (assoc word :units (mapv (fn [text a b] {:text text :start_cs a :end_cs b}) parts cuts (rest cuts))))) words))

(defn coverage-issues! [words mask issues start end]
  (let [covered (boolean-array (count mask))]
    (doseq [w words i (range (max 0 (- (:start_cs w) 15)) (min (count mask) (+ (:end_cs w) 15)))] (aset-boolean covered i true))
    (let [active (filter #(and (nth mask %) (not (aget covered %)))
                         (range (max 0 (u/rounded (* start 100))) (min (count mask) (u/rounded (* end 100)))))
          runs (reduce (fn [runs i] (if (and (seq runs) (<= (- i (peek (peek runs))) 25))
                                     (conj (pop runs) (conj (peek runs) i)) (conj runs [i]))) [] active)]
      (doseq [run runs :when (and (>= (count run) 80) (>= (- (peek run) (first run)) 100))]
        (u/notice! issues "uncovered_activity" (/ (first run) 100.0) (/ (inc (peek run)) 100.0) "" "Señal con energía sin texto reconocido. Puede ser voz omitida o residuo instrumental.")))))
