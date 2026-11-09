(ns user
  (:require [clojure.repl :refer [apropos dir doc find-doc pst source]]
            [clojure.test :as test]
            [karaoke-ass.audio :as audio]
            [karaoke-ass.cli :as cli]
            [karaoke-ass.lyrics :as lyrics]
            [karaoke-ass.recognition :as recognition]
            [karaoke-ass.render :as render]
            [karaoke-ass.syllables :as syllables]
            [karaoke-ass.timing :as timing]
            [karaoke-ass.util :as util]))

(defn test-all
  "Load the test namespaces and run the suite without exiting the REPL."
  []
  (require 'karaoke-ass.pipeline-test :reload)
  (require 'karaoke-ass.recognition-test :reload)
  (test/run-tests 'karaoke-ass.pipeline-test 'karaoke-ass.recognition-test))

(comment
  (cli/main ["--help"])
  (syllables/split-word "canción" "es" {})
  (test-all))
