(ns karaoke-ass.audio
  (:require [clojure.java.io :as io]
            [clojure.java.shell :as shell]
            [karaoke-ass.util :as u])
  (:import [java.nio ByteBuffer ByteOrder]))

(def rate 16000)
(def hop 160)
(defn load-audio [path]
  (let [{:keys [exit out err]}
        (shell/sh "ffmpeg" "-nostdin" "-v" "error" "-i" (.getCanonicalPath (io/file path))
                  "-vn" "-ar" (str rate) "-ac" "1" "-f" "f32le" "pipe:1" :out-enc :bytes)]
    (when-not (zero? exit) (u/fail (str "FFmpeg no pudo leer el audio: " err)))
    (let [buf (.order (ByteBuffer/wrap out) ByteOrder/LITTLE_ENDIAN)
          samples (float-array (quot (alength ^bytes out) 4))]
      (.get (.asFloatBuffer buf) samples)
      (when (or (zero? (alength samples)) (some #(not (Float/isFinite %)) samples))
        (u/fail "El audio está vacío o contiene muestras no válidas."))
      samples)))
(defn envelope [samples]
  (let [n (alength ^floats samples) size (long (Math/ceil (/ n (double hop))))
        rms (mapv (fn [i] (Math/sqrt (/ (reduce + 0.0 (for [j (range (* i hop) (min n (* (inc i) hop)))]
                                                                     (let [x (double (aget ^floats samples j))] (* x x)))) hop)))
                  (range size))]
    (mapv (fn [i] (/ (reduce + (map #(nth rms (max 0 (min (dec size) %))) (range (- i 2) (+ i 3)))) 5.0))
          (range size))))
(defn activity [energy]
  (let [sorted (vec (sort energy)) index (* 0.95 (dec (count sorted)))
        lo (long index) hi (min (inc lo) (dec (count sorted)))
        percentile (+ (nth sorted lo) (* (- index lo) (- (nth sorted hi) (nth sorted lo))))
        threshold (max 0.0003 (* percentile 0.06))]
    (mapv #(> % threshold) energy)))
(defn active-slice [mask start end]
  (let [lo (quot start hop) hi (min (count mask) (quot (+ end (dec hop)) hop))
        found (filter #(nth mask %) (range lo hi))]
    (when (seq found)
      [(max start (* (- (first found) 15) hop)) (min end (* (+ (last found) 16) hop))])))
