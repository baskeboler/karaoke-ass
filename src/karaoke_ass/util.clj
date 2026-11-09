(ns karaoke-ass.util
  (:require [clojure.data.json :as json]
            [clojure.java.io :as io])
  (:import [java.nio.file Files StandardCopyOption CopyOption]
           [java.security MessageDigest]
           [java.text Normalizer Normalizer$Form]
           [java.util Locale]))

(def version "0.2.0")
(defn fail [message] (throw (ex-info message {})))
(defn finite? [x] (and (number? x) (Double/isFinite (double x))))
;; Match Python's ties-to-even quantization, including negative offsets.
(defn rounded [x] (long (Math/rint (double x))))
(defn nfc [s] (Normalizer/normalize s Normalizer$Form/NFC))
(defn lower [s] (.toLowerCase ^String s Locale/ROOT))
(defn read-json [path]
  (json/read-str (.replaceFirst (slurp path :encoding "UTF-8") "^\uFEFF" "") :key-fn keyword))
(defn atomic-json [path data]
  (let [target (.toPath (io/file path))
        tmp (Files/createTempFile (.getParent (.toAbsolutePath target)) ".karaoke-" ".tmp"
                                  (make-array java.nio.file.attribute.FileAttribute 0))]
    (try
      (spit (.toFile tmp) (str (json/write-str data :escape-unicode false) "\n") :encoding "UTF-8")
      (Files/move tmp target (into-array CopyOption [StandardCopyOption/ATOMIC_MOVE StandardCopyOption/REPLACE_EXISTING]))
      (finally (Files/deleteIfExists tmp)))))
(defn file-hash [path]
  (let [digest (MessageDigest/getInstance "SHA-256") buffer (byte-array 1048576)]
    (with-open [in (io/input-stream path)]
      (loop [] (let [n (.read in buffer)] (when (pos? n) (.update digest buffer 0 n) (recur)))))
    (apply str (map #(format "%02x" (bit-and 255 %)) (.digest digest)))))
(defn log [& xs] (binding [*out* *err*] (apply println xs) (flush)))
(defn notice! [issues code start end text message]
  (swap! issues conj {:code code :start (/ (rounded (* 1000 (max 0 start))) 1000.0)
                     :end (/ (rounded (* 1000 (max 0 end))) 1000.0) :text text :message message}))
