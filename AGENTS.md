# Repository Guidelines

## Project Structure & Module Organization

`src/karaoke_ass/` contains the current Clojure application: `cli.clj` handles arguments and orchestration; `audio.clj` decodes audio; `recognition.clj` owns windows, retries, and compatible checkpoints; `timing.clj` and `syllables.clj` estimate units; `render.clj` writes outputs; `lyrics.clj` handles optional LRCLIB corrections. `test/` contains Clojure tests. `legacy/whisper_worker.py` is only the persistent Whisper/PyTorch inference adapter. The root `karaoke-ass` script launches Clojure. `legacy/tests/` contains inference-adapter tests, and `legacy/requirements.txt` declares its optional dependencies. The superseded Python application has been removed. Consult `README.md`, `USO_LOCAL.md`, and `VALIDACION.md` for usage and validation notes. Treat local media and `results/` as working artifacts, not test fixtures.

## Build, Test, and Development Commands

- `mise trust && mise install`: enable the configuration and install Python 3.12.
- `mise run setup`: fetch Clojure dependencies (Java 17+ and Clojure CLI required).
- `mise run setup:speech`: additionally install Whisper/PyTorch for recognition.
- `mise run repl` or `clojure -M:repl`: start the development REPL with `dev/user.clj`; `(test-all)` runs the suite without exiting.
- `mise run test`: check FFmpeg and run the Clojure suite.
- `mise run test:lyrics`: run lyrics tests without requiring FFmpeg.
- `./karaoke-ass vocals.mp3 --output results/example`: generate a karaoke draft.
- `./karaoke-ass vocals.mp3 --from-json corrected.json --output results/corrected`: regenerate without Whisper.

Alternatively: `clojure -M:test`. Use `clojure -M:test lyrics` for offline lyric tests, `python3 -m unittest discover -s legacy/tests -p test_whisper_worker.py -v` for inference-adapter safeguards.

## Coding Style & Naming Conventions

Use idiomatic Clojure namespaces and kebab-case function names; retain snake_case JSON field names for compatibility. Use four-space indentation and snake_case names in the Python adapter. Keep modules focused on their pipeline stage. Preserve source-audio timestamp semantics. No formatter or linter is configured; avoid unrelated formatting changes.

## Testing Guidelines

Use `clojure.test` for application behavior, and `unittest`/`unittest.mock` for the Python inference adapter. Mock Whisper and HTTP requests; use temporary directories for generated output. Add regression tests for changed behavior, especially timing sums, offsets, checkpoint reuse, output protection, and timestamp-preserving corrections. No coverage percentage is configured. Automated checks do not establish lyric accuracy or listening verification.

## Commit & Pull Request Guidelines

History uses short, descriptive subjects, including `docs: translate README to English`; no mandatory prefix scheme is established. Keep commits focused. PRs should explain the behavior change, list verification commands and results, link relevant issues, and update usage documentation when options or output contracts change.

## Data & Runtime Safeguards

Preserve unrelated local media and edited transcriptions. Keep syllable timing explicitly estimated and listening review pending. Retain output locking, input-file protection, and checkpoint compatibility. Automatic CUDA recovery must catch only CUDA out-of-memory errors; explicit CUDA requests remain strict.
