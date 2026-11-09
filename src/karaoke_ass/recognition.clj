(ns karaoke-ass.recognition
  (:require [clojure.data.json :as json]
            [clojure.java.io :as io]
            [clojure.string :as str]
            [clojure.walk :as walk]
            [karaoke-ass.audio :as audio]
            [karaoke-ass.util :as u])
  (:import [java.nio ByteBuffer ByteOrder]
           [java.nio.file Files]
           [java.util.concurrent TimeUnit]))

(defn words-from-json
  ([data] (words-from-json data "es"))
  ([data fallback]
   (when-not (map? data) (u/fail "La transcripción debe ser un objeto JSON."))
   (let [sources (cond (vector? (:words data)) [data]
                       (vector? (:segments data)) (:segments data)
                       :else (u/fail "El JSON debe contener words o segments con tiempos de palabra."))]
     (vec (mapcat
            (fn [segment]
              (when-not (map? segment) (u/fail "Cada segmento debe ser un objeto."))
              (let [items (:words segment []) language (or (:language segment) (:language data) fallback)
                    language (if (#{"auto" "es/en"} language) fallback language)]
                (when-not (and (vector? items) (string? language)) (u/fail "words debe ser una lista y language una cadena."))
                (if (and (seq (:text segment)) (empty? items))
                  (let [a (:start segment 0) b (:end segment 0)]
                    (when-not (every? u/finite? [a b]) (u/fail "Tiempos no válidos en segmento sin palabras."))
                    [{:text "" :missing_segment true :start a :end b}])
                  (mapv (fn [word]
                          (when-not (map? word) (u/fail "Cada palabra debe ser un objeto."))
                          (let [text (get word :text (:word word)) p (:probability word)
                                lang (or (:language word) language)]
                            (when-not (and (string? text) (every? u/finite? [(:start word) (:end word)])
                                           (or (nil? p) (and (u/finite? p) (<= 0 p 1))) (string? lang))
                              (u/fail "Texto, idioma, tiempo o probabilidad de palabra no válido."))
                            {:text (u/nfc (str/trim text)) :start (:start word) :end (:end word) :language lang :probability p})) items)))) sources)))))

(defn open-engine
  "Start a single persistent Whisper inference process, without the Python application."
  [samples options]
  (let [attrs (make-array java.nio.file.attribute.FileAttribute 0)
        pcm (Files/createTempFile "karaoke-pcm-" ".f32" attrs)
        worker (Files/createTempFile "karaoke-whisper-" ".py" attrs)
        process (atom nil)]
    (try
      (let [buf (.order (ByteBuffer/allocate (* 4 (alength ^floats samples))) ByteOrder/LITTLE_ENDIAN)]
        (.put (.asFloatBuffer buf) ^floats samples)
        (with-open [out (io/output-stream (.toFile pcm))] (.write out (.array buf))))
      (with-open [in (io/input-stream (io/resource "whisper_worker.py")) out (io/output-stream (.toFile worker))] (io/copy in out))
      (let [python (or (System/getenv "KARAOKE_PYTHON")
                       ;; Keep the venv symlink: resolving it loses pyvenv.cfg discovery.
                       (when (.canExecute (io/file ".venv/bin/python")) (.getAbsolutePath (io/file ".venv/bin/python"))) "python3")
            builder (ProcessBuilder. ^java.util.List [python "-u" (str worker)])
            _ (.redirectError builder java.lang.ProcessBuilder$Redirect/INHERIT)
            proc (.start builder) _ (reset! process proc)
            reader (io/reader (.getInputStream proc) :encoding "UTF-8")
            writer (io/writer (.getOutputStream proc) :encoding "UTF-8")
            send! (fn [request] (.write writer (str (json/write-str request) "\n")) (.flush writer))
            receive! (fn [] (let [line (.readLine ^java.io.BufferedReader reader)]
                             (when-not line (u/fail "Whisper terminó sin responder. Comprueba KARAOKE_PYTHON y las dependencias de inferencia."))
                             (let [reply (json/read-str line :key-fn keyword)]
                               (when (:error reply) (u/fail (:error reply))) reply)))
            _ (send! (assoc (select-keys options [:model :language :device :threads]) :model_dir (some-> (:model-dir options) str) :pcm (str pcm)))
            ready (receive!) state (atom {:device (:device ready) :cuda_oom_fallback false})
            cleanup (fn []
                      (.destroyForcibly proc) (.waitFor proc)
                      (Files/deleteIfExists pcm) (Files/deleteIfExists worker))
            hook (Thread. ^Runnable cleanup)
            _ (.addShutdownHook (Runtime/getRuntime) hook)
            close! (fn []
                     (try
                       (.close writer)
                       (when-not (.waitFor proc 2 TimeUnit/SECONDS) (.destroyForcibly proc) (.waitFor proc))
                       (.close reader)
                       (finally
                         (.removeShutdownHook (Runtime/getRuntime) hook)
                         (cleanup))))]
        {:version (:version ready) :state state :close close!
         :transcribe (fn [lo hi prompt]
                       (send! {:lo lo :hi hi :prompt prompt})
                       (let [reply (receive!)]
                         (reset! state (select-keys reply [:device :cuda_oom_fallback])) (:result reply)))})
      (catch Throwable e
        (when-let [proc @process] (.destroyForcibly ^Process proc) (.waitFor ^Process proc))
        (Files/deleteIfExists pcm) (Files/deleteIfExists worker)
        (throw e)))))

(defn coverage [result offset mask core-start core-end]
  (let [lo (u/rounded (* core-start 100)) hi (min (count mask) (u/rounded (* core-end 100)))
        covered (boolean-array (max 0 (- hi lo))) probabilities (atom [])]
    (doseq [segment (:segments result) word (:words segment)
            :let [a (+ (:start word) offset) b (min (+ (:end word) offset) (+ a 3))
                  x (max 0 (- (u/rounded (* a 100)) lo 10))
                  y (min (alength covered) (+ (- (u/rounded (* b 100)) lo) 10))]
            :when (> y x)]
      (doseq [i (range x y)] (aset-boolean covered i true))
      (swap! probabilities conj (:probability word 0.5)))
    (let [active (count (filter #(nth mask %) (range lo hi)))
          both (count (filter #(and (nth mask %) (aget covered (- % lo))) (range lo hi)))]
      [(/ both (double (max 1 active))) (if (seq @probabilities) (/ (reduce + @probabilities) (double (count @probabilities))) 0)
       (/ active 100.0)])))

(defn retry-result [engine result offset mask options start end core-start core-end]
  (let [segments
        (vec (mapcat
               (fn [sub-start]
                 (let [sub-end (min core-end (+ sub-start 12))
                       lo (u/rounded (* (max start (- sub-start 0.5)) audio/rate))
                       hi (u/rounded (* (min end (+ sub-end 0.5)) audio/rate))]
                   (when-let [[lo hi] (audio/active-slice mask lo hi)]
                     (let [alt ((:transcribe engine) lo hi nil) sub-offset (/ lo (double audio/rate))]
                       (keep (fn [segment]
                               (let [words (vec (keep (fn [w]
                                                       (let [a (+ (:start w) sub-offset) b (+ (:end w) sub-offset)]
                                                         (when (<= sub-start (/ (+ a b) 2.0) (Math/nextDown (double sub-end)))
                                                           (assoc w :start (- a offset) :end (- b offset) :language (:language alt))))) (:words segment)))]
                                 (when (seq words) (assoc segment :words words :language (:language alt)
                                                                :start (:start (first words)) :end (:end (peek words)))))) (:segments alt))))))
               (range core-start core-end 12)))]
    {:language (:language result) :segments segments}))

(defn signature-key [signature]
  ;; Python compares JSON integer and floating-point numbers by value.
  (walk/postwalk #(if (number? %) (double %) %) signature))

(defn recognize [samples mask options checkpoint source-hash]
  (let [engine (open-engine samples options)]
    (try
      (let [duration (/ (alength ^floats samples) (double audio/rate))
            start (:start options) end (min duration (or (:end options) duration))
            signature {:schema 1 :sha256 source-hash :model (:model options) :language (:language options)
                       :prompt (:prompt options) :chunk_seconds (:chunk-seconds options) :context 2
                       :start start :end end :whisper (:version engine) :temperature 0 :no_speech_threshold nil :retry_enabled (not (:no-retry options))}
            previous (when (and (.exists (io/file checkpoint)) (not (:no-resume options))) (u/read-json checkpoint))
            ;; JSON object keys become keywords; retain the Python numeric window-key convention.
            cached (atom (if (= (signature-key signature) (signature-key (update (:signature previous) :retry_enabled #(if (nil? %) true %)))) (:windows previous {}) {}))
            save! #(u/atomic-json checkpoint {:signature signature :windows @cached})
            count-windows (long (Math/ceil (/ (- end start) (:chunk-seconds options))))
            windows (atom []) all-segments (atom [])]
        (when (seq @cached) (u/log (str "Reanudando: " (count @cached) " tramos guardados.")))
        (dotimes [index count-windows]
          (let [core-start (+ start (* index (:chunk-seconds options))) core-end (min end (+ core-start (:chunk-seconds options)))
                lo (u/rounded (* (max start (- core-start 2)) audio/rate))
                hi (u/rounded (* (min end (+ core-end 2)) audio/rate))
                bounds (audio/active-slice mask lo hi)]
            (u/log (format "Tramo %d/%d: %.1f–%.1f s" (inc index) count-windows (double core-start) (double core-end)))
            (if-not bounds
              (swap! windows conj {:start core-start :end core-end :language nil :silent true})
              (let [[lo hi] bounds offset (/ lo (double audio/rate)) cache-key (keyword (str index))
                    result (or (get @cached cache-key)
                               (let [r ((:transcribe engine) lo hi (not-empty (:prompt options)))]
                                 (swap! cached assoc cache-key r) (save!) r))
                    [pc pf active] (coverage result offset mask core-start core-end)
                    result (if (and (not (:no-retry options)) (>= active 1) (< pc 0.65) (not (:_retry_checked result)))
                             (let [retry (retry-result engine result offset mask options start end core-start core-end)
                                   [rc rf] (coverage retry offset mask core-start core-end)
                                   use-retry (and (> rc (+ pc 0.1)) (>= rf 0.2) (> (+ (* 0.75 rc) (* 0.25 rf)) (+ (* 0.75 pc) (* 0.25 pf) 0.05)))
                                   selected (assoc (if use-retry retry result) :_alternative (if use-retry result retry)
                                                   :_retry_checked true :_retry_diagnostic {:primary_coverage pc :retry_coverage rc :selected (if use-retry "retry" "primary")})]
                               (swap! cached assoc cache-key selected) (save!) selected) result)
                    language (:language result (:language options))]
                (swap! windows conj {:start core-start :end core-end :language language :silent false :retry (:_retry_diagnostic result)
                                     :coverage (first (coverage result offset mask core-start core-end))})
                (doseq [segment (:segments result)]
                  (let [words (vec (keep (fn [w] (let [a (+ (:start w) offset) b (+ (:end w) offset) mid (/ (+ a b) 2.0)]
                                                  (when (or (and (<= core-start mid) (< mid core-end)) (and (= index (dec count-windows)) (= mid end)))
                                                    (assoc w :start a :end b :language (or (:language w) (:language segment) language))))) (:words segment)))]
                    (when (seq words) (swap! all-segments conj (assoc segment :words words :language language :start (:start (first words)) :end (:end (peek words)))))))))))
        (merge {:schema_version 1 :source_sha256 source-hash :language (:language options) :model (:model options)
                :windows @windows :segments @all-segments :interval [start end]} @(:state engine)))
      (finally ((:close engine))))))
