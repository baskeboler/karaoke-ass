# Whisper inference adapter

This directory contains the remaining Python code: `whisper_worker.py` and its
regression tests in `tests/`. The application itself runs in Clojure under
`src/karaoke_ass/`.

Clojure loads the worker from this classpath directory, starts a persistent Python
process, and exchanges JSON lines over stdin/stdout. The worker only loads
Whisper/PyTorch and transcribes supplied sample ranges. It retains automatic CUDA
out-of-memory recovery; explicit CUDA requests remain strict.

Install optional inference dependencies from the project root:

```bash
.venv/bin/python -m pip install -r legacy/requirements.txt
```

Run adapter tests without Whisper or a GPU:

```bash
python3 -m unittest discover -s legacy/tests -p test_whisper_worker.py -v
```

`--from-json` regeneration does not start this adapter or require Python.
