# Repository Guidelines

## Project Structure & Module Organization

`src/karaoke_ass/` contains the Python package: `cli.py` handles arguments and orchestration; `audio.py` decodes audio; `recognition.py` manages Whisper and checkpoints; `timing.py` and `syllables.py` estimate units; `render.py` writes outputs; `lyrics.py` handles optional LRCLIB corrections. `tests/` contains pipeline and lyrics tests. The root `karaoke-ass` script launches the package. Consult `README.md`, `USO_LOCAL.md`, and `VALIDACION.md` for usage and validation notes. `silabas.ejemplo.json` demonstrates overrides. Treat local media and `results/` as working artifacts, not test fixtures.

## Build, Test, and Development Commands

- `mise trust && mise install`: enable the configuration and install Python 3.12.
- `mise run setup`: create/reuse `.venv` and install the package in editable mode.
- `mise run setup:speech`: additionally install Whisper/PyTorch for recognition.
- `mise run test`: check FFmpeg and run the full unittest suite.
- `mise run test:lyrics`: run lyrics tests without requiring FFmpeg.
- `./karaoke-ass vocals.mp3 --output results/example`: generate a karaoke draft.
- `./karaoke-ass vocals.mp3 --from-json corrected.json --output results/corrected`: regenerate without Whisper.

Alternatively: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions/modules, and `PascalCase` classes. Follow surrounding Python conventions and keep modules focused on their pipeline stage. Preserve existing JSON field names and source-audio timestamp semantics. No formatter or linter is configured; avoid unrelated formatting changes.

## Testing Guidelines

Use `unittest` and `unittest.mock`; name files `test_*.py` and methods `test_*`. Mock Whisper and HTTP requests; use temporary directories for generated output. Add regression tests for changed behavior, especially timing sums, offsets, checkpoint reuse, output protection, and timestamp-preserving corrections. No coverage percentage is configured. Automated checks do not establish lyric accuracy or listening verification.

## Commit & Pull Request Guidelines

History uses short, descriptive subjects, including `docs: translate README to English`; no mandatory prefix scheme is established. Keep commits focused. PRs should explain the behavior change, list verification commands and results, link relevant issues, and update usage documentation when options or output contracts change.

## Data & Runtime Safeguards

Preserve unrelated local media and edited transcriptions. Keep syllable timing explicitly estimated and listening review pending. Retain output locking, input-file protection, and checkpoint compatibility. Automatic CUDA recovery must catch only CUDA out-of-memory errors; explicit CUDA requests remain strict.
