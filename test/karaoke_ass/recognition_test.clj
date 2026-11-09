(ns karaoke-ass.recognition-test
  (:require [clojure.test :refer [deftest is]]
            [clojure.java.io :as io]
            [karaoke-ass.audio :as audio]
            [karaoke-ass.cli :as cli]
            [karaoke-ass.pipeline-test :refer [with-temp]]
            [karaoke-ass.recognition :as r]
            [karaoke-ass.util :as u]))

(defn mock-engine [calls f]
  {:version "test" :state (atom {:device "cpu" :cuda_oom_fallback false}) :close (fn [])
   :transcribe (fn [lo hi prompt] (swap! calls conj [lo hi prompt]) (f lo hi prompt))})
(deftest ownership-resume-and-invalidation
  (with-temp root
    (let [checkpoint (io/file root "checkpoint.json") samples (float-array 160000) mask (vec (repeat 1000 true)) calls (atom [])
          options (assoc cli/defaults :chunk-seconds 5.0 :device "cpu" :no-retry true)
          engine (mock-engine calls (fn [lo _ _] (let [offset (/ lo 16000.0)]
                                                   {:language "en" :segments [{:words [{:word " hi" :start (- 4.8 offset) :end (- 5.2 offset)}
                                                                                       {:word " hi" :start (- 5.4 offset) :end (- 5.8 offset)}]}]})))]
      (with-redefs [r/open-engine (fn [& _] engine)]
        (is (= 2 (count (r/words-from-json (r/recognize samples mask options checkpoint "hash") "en"))))
        (is (= 2 (count @calls)))
        (r/recognize samples mask options checkpoint "hash") (is (= 2 (count @calls)))
        (r/recognize samples mask (assoc options :device "auto" :font "Other") checkpoint "hash") (is (= 2 (count @calls)))
        (r/recognize samples mask (assoc options :prompt "changed") checkpoint "hash") (is (= 4 (count @calls)))
        (r/recognize samples mask (assoc options :no-resume true) checkpoint "hash") (is (= 6 (count @calls)))))))
(deftest retry-selection-and-checkpoint
  (with-temp root
    (let [calls (atom []) checkpoint (io/file root "checkpoint.json") options (assoc cli/defaults :chunk-seconds 5.0 :prompt "hint")
          engine (mock-engine calls (fn [_ hi prompt]
                                      (if prompt {:language "es" :segments []}
                                        {:language "es" :segments [{:words [{:word " canción" :start 0.1 :end (- (/ hi 16000.0) 0.1) :probability 0.95}]}]})))
          samples (float-array 80000) mask (vec (repeat 500 true))]
      (with-redefs [r/open-engine (fn [& _] engine)]
        (let [result (r/recognize samples mask options checkpoint "hash")]
          (is (= "retry" (get-in result [:windows 0 :retry :selected])))
          (is (= ["hint" nil] (mapv #(nth % 2) @calls)))
          (is (= 1 (count (:windows (u/read-json checkpoint)))))
          (r/recognize samples mask options checkpoint "hash") (is (= 2 (count @calls))))))))
(deftest failed-work-keeps-completed-checkpoints-and-closes-engine
  (with-temp root
    (let [checkpoint (io/file root "checkpoint.json") calls (atom []) closed (atom false)
          engine (assoc (mock-engine calls (fn [lo _ _] (if (pos? lo) (throw (ex-info "unrelated CUDA error" {}))
                                                         {:language "en" :segments [{:words [{:word "hello" :start 1 :end 2}]}]})))
                        :close #(reset! closed true))]
      (with-redefs [r/open-engine (fn [& _] engine)]
        (is (thrown-with-msg? Exception #"unrelated" (r/recognize (float-array 160000) (vec (repeat 1000 true))
                                                                              (assoc cli/defaults :chunk-seconds 5.0 :no-retry true) checkpoint "hash"))))
      (is @closed) (is (= 1 (count (:windows (u/read-json checkpoint))))))))
(deftest silent-windows-do-not-transcribe
  (with-temp root
    (let [calls (atom []) engine (mock-engine calls (fn [& _] (throw (Exception. "unexpected"))))]
      (with-redefs [r/open-engine (fn [& _] engine)]
        (let [result (r/recognize (float-array 80000) (vec (repeat 500 false)) cli/defaults (io/file root "checkpoint.json") "hash")]
          (is (empty? (:segments result))) (is (:silent (first (:windows result)))) (is (empty? @calls)))))))

(deftest python-checkpoint-compatibility
  (with-temp root
    (let [checkpoint (io/file root "checkpoint.json") calls (atom []) engine (mock-engine calls (fn [& _] (throw (Exception. "must reuse cache"))))
          options (assoc cli/defaults :chunk-seconds 5.0 :no-retry false)
          ;; Python's argparse default start is integer 0, and old schemas omit retry_enabled.
          signature {:schema 1 :sha256 "hash" :model "medium" :language "auto" :prompt "" :chunk_seconds 5 :context 2
                     :start 0 :end 5 :whisper "test" :temperature 0 :no_speech_threshold nil}]
      (u/atomic-json checkpoint {:signature signature :windows {:0 {:language "en" :segments [{:words [{:word "hello" :start 1 :end 2}]}] :_retry_checked true}}})
      (with-redefs [r/open-engine (fn [& _] engine)]
        (is (= 1 (count (:segments (r/recognize (float-array 80000) (vec (repeat 500 true)) options checkpoint "hash")))))
        (is (empty? @calls))))))

(deftest fallback-device-metadata-survives-assembly
  (with-temp root
    (let [calls (atom []) state (atom {:device "cuda" :cuda_oom_fallback false})
          engine (assoc (mock-engine calls (fn [lo _ _]
                                            (when (pos? lo) (reset! state {:device "cpu" :cuda_oom_fallback true}))
                                            {:language "en" :segments [{:words [{:word "hello" :start 1 :end 2}]}]})) :state state)]
      (with-redefs [r/open-engine (fn [& _] engine)]
        (let [result (r/recognize (float-array 160000) (vec (repeat 1000 true)) (assoc cli/defaults :chunk-seconds 5.0 :no-retry true)
                                  (io/file root "checkpoint.json") "hash")]
          (is (= "cpu" (:device result))) (is (:cuda_oom_fallback result))
          (is (= 2 (count @calls))) (is (= 2 (count (:windows (u/read-json (io/file root "checkpoint.json")))))))))))
