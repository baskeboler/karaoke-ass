(ns karaoke-ass.test-runner
  (:require [clojure.test :as test]
            [karaoke-ass.pipeline-test]
            [karaoke-ass.recognition-test]))
(defn -main [& args]
  (let [result (if (= args '("lyrics"))
                 (binding [test/*report-counters* (ref test/*initial-report-counters*)]
                   (test/test-vars [#'karaoke-ass.pipeline-test/conservative-lyrics])
                   (let [result @test/*report-counters*] (test/do-report (assoc result :type :summary)) result))
                 (test/run-tests 'karaoke-ass.pipeline-test 'karaoke-ass.recognition-test))]
    (shutdown-agents)
    (System/exit (if (test/successful? result) 0 1))))
