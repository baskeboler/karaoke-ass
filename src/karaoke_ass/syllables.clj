(ns karaoke-ass.syllables
  (:require [clojure.string :as str] [karaoke-ass.util :as u]))

(def vowels (set "aeiouáéíóúü"))
(def open-vowels (set "aeoáéó"))
(def clusters #{"bl" "br" "cl" "cr" "dr" "fl" "fr" "gl" "gr" "pl" "pr" "tr" "ch" "ll" "rr" "qu" "gu"})
(defn key-text [s] (u/lower (u/nfc s)))
(defn validate-overrides [data]
  (when-not (map? data) (u/fail "El diccionario silábico debe ser un objeto JSON."))
  (into {} (for [[word parts] data :let [word (if (keyword? word) (name word) word)]]
             (do (when-not (and (string? word) (seq word) (vector? parts) (seq parts)
                                (every? #(and (string? %) (seq %)) parts)
                                (= (key-text word) (key-text (apply str parts))))
                   (u/fail (str "División no válida para " word ": las partes deben reconstruir la palabra.")))
                 [(key-text word) parts]))))
(defn spanish-cuts [word]
  (let [s (u/lower word) n (count s)
        positions (filter (fn [i] (let [c (nth s i)] (and (vowels c)
                                   (not (and (= c \u) (pos? i) (#{\q \g} (nth s (dec i)))
                                             (< (inc i) n) ((set "eiéí") (nth s (inc i)))))))) (range n))
        nuclei (reduce (fn [groups i]
                         (let [g (peek groups) prev (peek g) a (when prev (nth s prev)) b (nth s i)
                               adjacent (and prev (or (= i (inc prev)) (= "h" (subs s (inc prev) i))))
                               hiatus (or (and (open-vowels a) (open-vowels b)) (#{\í \ú} a) (#{\í \ú} b) (= a b))
                               join (or (= 1 (count g)) (and (= 2 (count g)) ((set "iuü") (nth s (first g)))
                                                                           (open-vowels a) ((set "iuü") b)))]
                           (if (and adjacent (not hiatus) join) (conj (pop groups) (conj g i)) (conj groups [i])))) [] positions)]
    (vec (concat [0] (for [[left right] (partition 2 1 nuclei)
                          :let [a (peek left) b (first right) cluster (subs s (inc a) b)]]
                      (cond (empty? cluster) b (= 1 (count cluster)) (inc a)
                            :else (- b (if (clusters (subs cluster (- (count cluster) 2))) 2 1)))) [n]))))
(defn english-cuts [word]
  (let [s (u/lower word) n (count s)]
    (if (re-find #"['’]" s) [0 n]
      (let [matcher (re-matcher #"[aeiouy]+" s)
            groups (loop [out []] (if (.find matcher) (recur (conj out [(.start matcher) (.end matcher) (.group matcher)])) out))
            groups (if (and (> (count groups) 1) (str/ends-with? s "e") (not (re-find #"[^aeiou]le$" s))
                            (= (first (peek groups)) (dec n))) (pop groups) groups)
            groups (if (and (> (count groups) 1) (str/ends-with? s "ed") (not (re-find #"[td]ed$" s))
                            (= (peek groups) [(- n 2) (dec n) "e"])) (pop groups) groups)]
        (vec (sort (set (concat [0 n] (for [[left right] (partition 2 1 groups)] (max (second left) (dec (first right))))))))))))
(defn split-word [text language overrides]
  (let [[_ prefix word suffix] (re-matches #"(?sU)([^\p{L}\p{N}_]*)(.*?)([^\p{L}\p{N}_]*)" text)]
    (if (empty? word) [text]
      (let [override (get overrides (key-text word))
            cuts (cond override (vec (reductions + 0 (map count override)))
                       (= language "es") (spanish-cuts word) (= language "en") (english-cuts word) :else [0 (count word)])
            parts (vec (for [[a b] (partition 2 1 cuts) :when (< a b)] (subs word a b)))
            parts (if (seq parts) parts [word])]
        (-> parts (update 0 #(str prefix %)) (update (dec (count parts)) #(str % suffix)))))))
