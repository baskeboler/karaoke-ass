(ns karaoke-ass.pipeline-test
  (:require [clojure.test :refer [deftest is testing]]
            [clojure.java.io :as io]
            [clojure.java.shell :as shell]
            [clojure.string :as str]
            [karaoke-ass.audio :as audio]
            [karaoke-ass.cli :as cli]
            [karaoke-ass.lyrics :as lyrics]
            [karaoke-ass.recognition :as recognition]
            [karaoke-ass.render :as render]
            [karaoke-ass.syllables :as syllables]
            [karaoke-ass.timing :as timing]
            [karaoke-ass.util :as u]))

(defmacro with-temp [binding & body]
  `(let [~binding (.toFile (java.nio.file.Files/createTempDirectory "karaoke-test-" (make-array java.nio.file.attribute.FileAttribute 0)))]
     (try ~@body (finally (doseq [file# (reverse (file-seq ~binding))] (io/delete-file file# true))))))
(defn word [text start end & [language]]
  {:text text :start start :end end :language (or language "es") :probability 0.9})
(defn words [text]
  (mapv (fn [i t] (assoc (word t i (+ i 0.8) "en") :start_cs (* i 100) :end_cs (+ (* i 100) 80))) (range) (str/split text #"\s+")))
(defn cs [text]
  (let [[h m s] (str/split text #":")] (u/rounded (* 100 (+ (* 3600 (Long/parseLong h)) (* 60 (Long/parseLong m)) (Double/parseDouble s))))))
(defn assert-ass [ass]
  (let [events (filter #(str/starts-with? % "Dialogue:") (str/split-lines ass))]
    (is (seq events))
    (doseq [line events :let [fields (str/split line #"," 10) start (cs (nth fields 1)) end (cs (nth fields 2))
                             durations (map #(Long/parseLong (second %)) (re-seq #"\\k(?:f|o)?(\d+)" (nth fields 9)))]]
      (is (<= 0 start)) (is (> end start)) (is (= (- end start) (reduce + durations))))))

(deftest syllable-rules
  (doseq [[text expected] {"paquete" ["pa" "que" "te"] "guitarra" ["gui" "ta" "rra"] "canción" ["can" "ción"]
                         "tierra" ["tie" "rra"] "día" ["dí" "a"] "ahora" ["a" "ho" "ra"] "poeta" ["po" "e" "ta"]
                         "buey" ["buey"] "prohibir" ["prohi" "bir"] "país" ["pa" "ís"]}]
    (is (= expected (syllables/split-word text "es" {})) text))
  (is (= ["¡SPO" "TI" "FY!"] (syllables/split-word "¡SPOTIFY!" "es" (syllables/validate-overrides {:Spotify ["Spo" "ti" "fy"]}))))
  (doseq [text ["“corazón,”" "..." "¿día?" "Über" "👩🎤Hola!"]]
    (is (= text (apply str (syllables/split-word text "es" {})))))
  (doseq [[text expected] {"baby" ["ba" "by"] "make" ["make"] "don't" ["don't"] "wanted" ["wan" "ted"]}]
    (is (= expected (syllables/split-word text "en" {}))))
  (is (= ["bonjour"] (syllables/split-word "bonjour" "fr" {})))
  (doseq [bad [{:hola ["ho" "lo"]} {:x []} {:x [""]} []]] (is (thrown? Exception (syllables/validate-overrides bad)))))

(deftest timing-normalization
  (let [issues (atom []) out (timing/normalize-words [(word "sí" 0 0.4) (word "sí" 0.02 0.42) (word "sí" 0.5 0.9)] 1 issues)]
    (is (= 2 (count out))) (is (some #(= "duplicate" (:code %)) @issues)))
  (let [out (timing/normalize-words [(word "uno" 0 0.5) (word "dos" 2 2)] 3 (atom []))]
    (is (= 1 (count out))) (is (= 50 (:end_cs (first out)))))
  (let [issues (atom []) out (timing/normalize-words [(word "mi" 0 0.8) (word "voz" 0.8 0.8) (word "canta" 0.75 1.3)] 2 issues)]
    (is (= 3 (count out))) (is (some #(= "timing_repaired" (:code %)) @issues))
    (is (every? (fn [[a b]] (<= (:end_cs a) (:start_cs b))) (partition 2 1 out))))
  (is (thrown? Exception (timing/normalize-words [(word "bad" 2 1)] 3 (atom []))))
  (let [rng (java.util.Random. 29)]
    (dotimes [_ 100]
      (let [raw (mapv (fn [i] (let [a (/ (u/rounded (* 500 (.nextDouble rng))) 100.0)]
                                (word (str i) a (min 5 (+ a (nth [0 0.01 0.4 1] (.nextInt rng 4))))))) (range 50))
            out (timing/normalize-words raw 5 (atom []))]
        (is (every? #(<= 0 (:start_cs %) (dec (:end_cs %)) 499) out))
        (is (every? (fn [[a b]] (<= (:end_cs a) (:start_cs b))) (partition 2 1 out)))))))
(deftest input-validation
  (doseq [value [Double/NaN Double/POSITIVE_INFINITY nil "1" true]]
    (is (thrown? Exception (recognition/words-from-json {:words [(word "bad" value 2)]}))))
  (doseq [data [[] {:segments [1]} {:segments [{:words [nil]}]} {:words [{}]} {:words [(assoc (word "bad" 0 1) :probability 2)]}]]
    (is (thrown? Exception (recognition/words-from-json data))))
  (is (:missing_segment (first (recognition/words-from-json {:segments [{:text "missing" :start 0 :end 1}]})))))
(deftest unit-boundaries
  (doseq [width [3 4 10 101] n (range 1 (inc (min width 5)))]
    (let [cuts (timing/boundaries 2 (+ 2 width) n (vec (repeat 200 1.0)))]
      (is (= [2 (+ 2 width)] [(first cuts) (peek cuts)])) (is (every? (fn [[a b]] (< a b)) (partition 2 1 cuts)))))
  (let [issues (atom []) ws (timing/normalize-words [(word "paquete" 0 0.01)] 1 issues)
        units (timing/timed-units ws [1.0] {} "syllable" issues)]
    (is (= 1 (count (:units (first units))))) (is (some #(= "too_short" (:code %)) @issues))))

(deftest short-energy-and-coverage-report
  (doseq [length [1 80 160 161 500]]
    (let [energy (audio/envelope (float-array length)) mask (audio/activity energy)]
      (is (= (long (Math/ceil (/ length 160.0))) (count energy)))
      (is (every? zero? energy)) (is (not-any? true? mask))))
  (is (= [1440 8160] (audio/active-slice (vec (concat (repeat 24 false) (repeat 12 true) (repeat 64 false))) 0 16000)))
  (let [issues (atom []) mask (vec (repeat 400 true))]
    (timing/coverage-issues! [] mask issues 1 3)
    (is (= "uncovered_activity" (:code (first @issues))))
    (is (== 1 (:start (first @issues)))) (is (== 3 (:end (first @issues))))))

(deftest rendering-offset-preview-and-safety
  (let [ws (timing/normalize-words [(word "Mi" 0.1 0.3) (word "corazón" 0.6 1.6) (word "canta." 1.8 2.4)] 3 (atom []))
        units (timing/timed-units ws (vec (repeat 300 1.0)) {} "syllable" (atom []))]
    (doseq [effect ["k" "kf" "ko"] offset [-0.65 0 0.19]]
      (let [[ass rows text] (render/render units (assoc cli/defaults :effect effect :offset offset))]
        (assert-ass ass) (is (seq rows)) (is (seq text)) (is (every? #(< (:inicio_s %) (:fin_s %)) rows))))
    (is (str/includes? (first (render/render units cli/defaults)) "{\\k30}")))
  (is (= "｛＼pos(0,0)｝Hi＼Nbye" (render/safe-text "{\\pos(0,0)}Hi\\Nbye")))
  (let [units (timing/timed-units (timing/normalize-words [(word "first" 1 2) (word "second" 2 3)] 4 (atom [])) (vec (repeat 400 1.0)) {} "word" (atom []))]
    (doseq [effect ["k" "kf" "ko"] offset [0 0.3 -1.1]]
      (let [[ass rows text] (render/render units (assoc cli/defaults :effect effect :offset offset :max-chars 5))
            events (filter #(str/starts-with? % "Dialogue:") (str/split-lines ass))]
        (assert-ass ass)
        (doseq [[event row txt] (map vector events rows text)
                :let [fields (str/split event #"," 10) start (cs (nth fields 1)) singing (u/rounded (* (:inicio_s row) 100))]]
          (is (= start (max 0 (- singing 20))))
          (when (> singing start) (is (str/starts-with? (nth fields 9) (str "{\\k" (- singing start) "}"))))
          (is (= singing (cs (second (re-find #"\[([^ ]+)" txt))))))))))

(deftest conservative-lyrics
  (let [original (words "we will survuve this night") [out changes unresolved] (lyrics/correct-words original "we will survive this night")]
    (is (= "survive" (:text (nth out 2)))) (is (= 1 (count changes))) (is (empty? unresolved))
    (is (= (map #(dissoc % :text) original) (map #(dissoc % :text) out)))
    (doseq [reference ["we will dance this night" "we will always survive this night" "we will this night"]]
      (let [[out changes unresolved] (lyrics/correct-words original reference)]
        (is (= original out)) (is (empty? changes)) (is (seq unresolved)))))
  (is (empty? (second (lyrics/correct-words (words "we will survuve this night we will survive this night") "we will survive this night we will survive this night"))))
  (is (empty? (nth (lyrics/correct-words (words "we will survive this night") "intro words We will survive this night! final verse") 2)))
  (doseq [record [{:id 42 :artistName " Artist " :trackName "TITLE" :plainLyrics "my lyrics"}
                  {:id 42 :artistName "Artist" :trackName "Title" :syncedLyrics "[ar:Artist]\n[00:01.00]hello world\n[00:02.00]next line"}]]
    (with-redefs [lyrics/fetch-record (fn [& _] record)] (is (= 42 (:id (second (lyrics/fetch-lyrics "artist" "title")))))))
  (doseq [update [{:artistName "other"} {:instrumental true} {:plainLyrics nil} {:id nil} {:id true}]]
    (with-redefs [lyrics/fetch-record (fn [& _] (merge {:id 42 :artistName "Artist" :trackName "Title" :plainLyrics "my lyrics"} update))]
      (is (thrown? Exception (lyrics/fetch-lyrics "artist" "title")))))
  (with-redefs [lyrics/fetch-lyrics (fn [& _] (throw (java.io.IOException. "offline")))]
    (let [ws (words "we will survive this night") issues (atom []) [out report] (lyrics/apply-lyrics ws "A" "T" issues)]
      (is (= ws out)) (is (= "unavailable" (:status report))) (is (= "lyrics_unavailable" (:code (first @issues)))))))

(deftest cli-validation-and-protection
  (with-temp root
    (let [audio (io/file root "input.wav") _ (spit audio "")]
      (doseq [args [["--artist" "A"] ["--title" "T"] ["--artist" " " "--title" "T"] ["--device" "gpu"] ["--offset" "NaN"] ["--font" "bad,font"] ["--chunk-seconds" "25"]]]
        (is (thrown? Exception (cli/validate (cli/parse-args (into [(str audio)] args))))))
      (is (= 0 (cli/main ["--help"])))
      (is (thrown? Exception (cli/parse-args ["--unknown"])))
      (cli/with-output-lock root #(is (thrown? Exception (cli/with-output-lock root (fn [] nil)))))
      (let [input (io/file root "transcripcion.json") _ (spit input "{}")]
        (is (thrown-with-msg? Exception #"entrada" (cli/run (cli/parse-args [(str audio) "--from-json" (str input) "-o" (str root) "--overwrite"]))))))))

(deftest output-lock-excludes-another-jvm
  (with-temp root
    (let [expression "(require '[karaoke-ass.cli :as cli]) (System/exit (try (cli/with-output-lock (System/getenv \"KARAOKE_LOCK_TEST\") (fn [] nil)) 1 (catch Exception _ 0)))"
          builder (ProcessBuilder. ^java.util.List [(str (System/getProperty "java.home") "/bin/java") "-cp" (System/getProperty "java.class.path") "clojure.main" "-e" expression])]
      (.put (.environment builder) "KARAOKE_LOCK_TEST" (str root))
      (cli/with-output-lock root
        (fn []
          (let [process (.start builder)]
            (try
              (is (.waitFor process 30 java.util.concurrent.TimeUnit/SECONDS))
              (is (= 0 (.exitValue process)))
              (finally (.destroyForcibly process)))))))))

(deftest real-decode-json-regeneration
  (with-temp root
    (let [audio (io/file root "audio with spaces.wav") source (io/file root "words.json") output (io/file root "out") corrected (io/file root "corrected")
          result (shell/sh "ffmpeg" "-v" "error" "-f" "lavfi" "-i" "sine=frequency=220:duration=3" "-y" (str audio))]
      (is (zero? (:exit result)))
      (u/atomic-json source {:language "es" :words [(word "Hola," 0.2 0.8) (word "canción." 1 2)]})
      (let [options (cli/parse-args [(str audio) "--from-json" (str source) "-o" (str output)])]
        (is (= 0 (cli/run options)))
        (assert-ass (slurp (io/file output "karaoke.ass")))
        (is (false? (:verified_by_listening (u/read-json (io/file output "revision.json")))))
        (is (thrown? Exception (cli/run options)))
        (is (= 0 (cli/run (assoc options :from-json (str (io/file output "transcripcion.json")) :output (str corrected)))))
        (is (= (slurp (io/file output "karaoke.ass")) (slurp (io/file corrected "karaoke.ass"))))
        (u/atomic-json source {:source_sha256 "mismatch" :words [(word "Hola" 0.2 0.8)]})
        (is (thrown-with-msg? Exception #"SHA-256" (cli/run (assoc options :output (str (io/file root "wrong"))))))))))

(deftest lyrics-persist-before-render-and-network-is-opt-in
  (with-temp root
    (let [audio (io/file root "audio.wav") _ (spit audio "") source (io/file root "words.json") calls (atom 0)
          raw (mapv #(dissoc % :start_cs :end_cs) (words "we will survuve this night"))]
      (u/atomic-json source {:words raw})
      (doseq [[enabled failure] [[false false] [true false] [true true]]]
        (let [output (io/file root (str enabled "-" failure))
              args (cond-> [(str audio) "--from-json" (str source) "--units" "word" "-o" (str output)] enabled (into ["--artist" "Artist" "--title" "Title"]))]
          (reset! calls 0)
          (with-redefs [audio/load-audio (fn [_] (float-array (repeat 96000 1.0)))
                        lyrics/fetch-lyrics (fn [& _]
                                              (swap! calls inc)
                                              (is (= "survuve" (get-in (u/read-json (io/file output "transcripcion.json")) [:words 2 :text])))
                                              (is (not (.exists (io/file output "karaoke.ass"))))
                                              (if failure (throw (java.io.IOException. "offline"))
                                                ["we will survive this night" {:provider "LRCLIB" :id 1 :url "https://lrclib.net/api/get/1"}]))]
            (is (= 0 (cli/run (cli/parse-args args)))))
          (is (= (if enabled 1 0) @calls))
          (let [saved (u/read-json (io/file output "transcripcion.json")) expected (if (and enabled (not failure)) "survive" "survuve")]
            (is (= expected (get-in saved [:words 2 :text]))) (is (== 2 (get-in saved [:words 2 :start])))
            (is (= "survuve" (get-in (u/read-json (io/file output "transcripcion.original.json")) [:words 2 :text])))
            (doseq [file ["karaoke.ass" "transcripcion.txt" "unidades.tsv"]] (is (str/includes? (slurp (io/file output file)) expected)))
            (doseq [file ["revision.json" "ejecucion.json"]]
              (is (= enabled (contains? (u/read-json (io/file output file)) :lyrics))))))))))
