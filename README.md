# Vocals → karaoke ASS

A local tool that turns a song's vocal track into ASS karaoke subtitles. It takes an audio file and produces `karaoke.ass`, timestamped text, syllable timings, and a review report. It does not require a paid API or send audio to a remote service. Whisper may download the selected model on first use.

**The result is an automatically generated draft.** Word timings come from Whisper. Spelling-based syllable splits and boundaries within words are estimated using Spanish/English rules and audio energy. They are not phonetic alignment and do not guarantee accurate lyrics. Linked vowels, melismas, simultaneous backing vocals, and unusual pronunciations require review.

## Installation

The application runs on **Clojure/JVM**. Requires Java 17 or later, [Clojure CLI](https://clojure.org/guides/install_clojure), and FFmpeg on PATH. Dependencies are declared in `deps.edn`: Clojure 1.12.4 and `org.clojure/data.json` 2.5.2. Dependency documentation was checked through Context7 against the Clojure and data.json repositories.

```bash
clojure -P -M:test
./karaoke-ass --help
clojure -M:test
```

`./karaoke-ass` is the Clojure launcher. Relative audio, JSON, dictionary, model, and output paths are relative to the directory from which you invoke it. `clojure -M:run …` also works from the project root. Set `KARAOKE_CLOJURE` to select another Clojure CLI executable.

**Regeneration with `--from-json` needs no Python, NumPy, or Whisper.** Once JVM dependencies are installed, it runs locally; optional `--artist`/`--title` still enable the LRCLIB request described below. The Clojure code decodes audio through FFmpeg, computes energy, normalizes timings, splits syllables, handles corrections/checkpoints/locking, and writes all output files.

Fresh recognition uses a small persistent Python inference adapter in `legacy/whisper_worker.py`, because the existing Whisper/PyTorch model runtime is Python. It only loads the model and transcribes sample ranges supplied by Clojure; all application logic is in `src/karaoke_ass/`. To enable it:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r legacy/requirements.txt
./karaoke-ass vocals.mp3 --model base --device cpu
```

The inference adapter selects `KARAOKE_PYTHON`, `.venv/bin/python`, or `python3`, in that order. An existing speech environment can be reused:

```bash
KARAOKE_PYTHON=/path/to/environment/bin/python ./karaoke-ass vocals.mp3
```

Whisper/PyTorch can require large downloads. Model downloads and their cache still use Whisper's normal behavior. Inference uses the existing version, `20250625`; no remote inference service is used.

## Development environment with mise

Install Java, Clojure CLI, FFmpeg, and [mise](https://mise.jdx.dev/getting-started.html). The mise configuration retains Python 3.12 and `.venv` for the optional inference adapter and its tests.

```bash
mise trust
mise install
mise run setup
mise run test
```

| Task | Purpose |
| --- | --- |
| `mise run setup` | Fetch Clojure development/test dependencies. |
| `mise run setup:speech` | Also install the optional Whisper/PyTorch runtime. |
| `mise run repl` | Start a Clojure REPL with `dev/user.clj` loaded automatically. |
| `mise run check:ffmpeg` | Check FFmpeg. |
| `mise run test` | Run the Clojure suite, including real FFmpeg decoding with synthetic audio. |
| `mise run test:lyrics` | Run Clojure lyric tests without FFmpeg or network requests. |
| `mise run test:inference` | Test the Python inference adapter's CUDA recovery with mocks; needs only Python's standard library. |

Clojure application code lives in `src/karaoke_ass/`, with tests in `test/karaoke_ass/`. The remaining Python code is confined to `legacy/`: the Whisper inference adapter and its tests. The superseded Python application, its tests, packaging, and migration comparison script have been removed. Invoke this repository's `./karaoke-ass` to use Clojure.

Start the development REPL with `mise run repl` or `clojure -M:repl`. The `:repl` alias adds `dev/` and `test/` to the classpath, so Clojure loads `dev/user.clj` automatically. The `user` namespace provides aliases for the application modules, standard REPL helpers such as `doc` and `source`, and `(test-all)` to run the suite without closing the REPL. For example, evaluate `(syllables/split-word "canción" "es" {})` or `(cli/main ["--help"])`.

## Usage

```bash
./karaoke-ass /path/to/vocals.mp3 --output ./results/my-song
```

This single command runs the entire process. By default, it uses Whisper `medium`, selects an available CPU or CUDA device, and detects the language per chunk. It preserves timestamps from the original file, even if the song starts with silence.

With `--device auto` (the default), if CUDA runs out of memory while loading the model, transcribing, or retrying a chunk, the process releases its references to the GPU model and retries that work on CPU. It keeps the same model and completed chunks; subsequent chunks run on CPU. The output and `ejecucion.json` record the switch. `--device cuda` requires a GPU: an out-of-memory error stops execution with an explanatory message.

If you encounter `CUDA out of memory`, you can continue directly on CPU:

```bash
KARAOKE_PYTHON=.venv/bin/python ./karaoke-ass vocals.mp3 --device cpu
```

The `medium` model requires roughly 5 GB of VRAM according to [Whisper](https://github.com/openai/whisper#available-models-and-languages). On a GPU with approximately 4 GB, use CPU to keep `medium`, or try `--model small --device cuda` (roughly 2 GB). Actual usage depends on the audio and other processes. Changing only the device allows saved chunks to be reused; changing the model invalidates that cache. CPU processing may take longer.

For a Spanish track with known proper names:

```bash
./karaoke-ass vocals.mp3 --language es --prompt 'Artist names, places, or brands.' --output results/song
```

For songs that switch between Spanish and English, keep `--language auto`. Detection happens per chunk, so it may fail when the language changes within a chunk. `--chunk-seconds 12` allows shorter chunks with less linguistic context. Forcing a language applies to the entire run and does not translate the lyrics.

```bash
./karaoke-ass vocals.mp3 --model small --device cpu --threads 4 --output results/test
./karaoke-ass vocals.mp3 --start 12 --end 35 --output results/excerpt
```

`small` is an option for trying the tool with fewer resources. Quality depends on the track; performance is not guaranteed. An excerpt retains its original timestamps rather than starting again at zero. Models are downloaded and stored through Whisper's standard cache; `--model-dir` selects another directory.

## Output files

| File | Contents |
| --- | --- |
| `karaoke.ass` | Karaoke with progressive `\kf` highlighting, two lines when needed, white text, and yellow highlighting. |
| `transcripcion.txt` | Text for each event with output timestamps. |
| `transcripcion.json` | Editable, normalized words and timings on the original audio timeline. |
| `transcripcion.original.json` | Original recognition output, before removing duplicates or repairing timings. |
| `unidades.tsv` | Start/end of each unit in the output and original audio. |
| `revision.txt`, `revision.json` | Warnings about low confidence, overly long words, repairs, possible omissions, and languages without syllable splitting. |
| `ejecucion.json` | Options used, audio SHA-256 hash, duration, and statistics. |
| `.checkpoint.json` | Recognition results per chunk, used to resume processing. |

The report always indicates that listening review is pending, even if there are no specific warnings. Whisper confidence is only a guide. Energy detection does not necessarily distinguish vocals from instrumental remnants.

## Correcting and regenerating

Edit **a copy** of `transcripcion.json`: you can change each word's `text`, `start`, `end`, and `language`. Timings are in seconds relative to the original audio. Each word can have `language: "es"` or `"en"`; other languages are preserved per word and flagged in the report.

```bash
./karaoke-ass vocals.mp3 --from-json corrected-lyrics.json --output results/corrected
```

This recalculates the ASS and units without loading Whisper. The audio hash is checked if the JSON includes it. Raw Whisper JSON with `segments[].words[]` is also supported. A lyrics file without timings is not supported: it requires additional alignment.

For names or borrowed words, create a dictionary of syllable splits that reconstruct each word exactly:

```json
{
  "Spotify": ["Spo", "ti", "fy"],
  "podcast": ["pod", "cast"]
}
```

```bash
./karaoke-ass vocals.mp3 --from-json corrected-lyrics.json --syllables syllables.json --output results/adjusted
```

The dictionary controls text splitting; it does not correct lyrics or determine exact phonetic boundaries. The English rules are especially approximate.

Presentation options:

```bash
./karaoke-ass vocals.mp3 --units word --effect k --offset 0.25 --output results/words
./karaoke-ass vocals.mp3 --font 'DejaVu Sans' --font-size 64 --max-chars 72 --max-duration 6 --output results/style
```

`--offset` shifts the ASS, timestamped text, and TSV output columns. Timings in the editable JSON and warnings remain relative to the original audio. A negative offset clips units that would fall before zero. `--effect` accepts `kf`, `k`, and `ko`. ASS durations are quantized to hundredths of a second and checked to ensure they add up exactly to each event's duration.

## Interruptions and reruns

If recognition is interrupted, repeat the same command. Checkpoint chunks are reused when the audio content, model, and recognition options match. Changing the audio, language, prompt, or chunk size invalidates that cache. Recalculating styles and syllables does not invalidate recognition.

If final output files already exist, the program stops to prevent accidental overwriting. Use another directory or `--overwrite`. `--no-resume` forces recognition to run again. A JVM/OS file lock prevents two Clojure runs from writing to the same destination simultaneously.

Inputs without usable words produce an error and preserve the original recognition output for diagnosis; an empty ASS is not generated. If an error occurs, check the message and checkpoint. Exit codes: 0, output generated; 2, error; 130, interrupted. Exit code 0 confirms that a valid draft was generated, not that its lyrics are accurate.

## Processing a directory

```bash
for track in /path/to/vocals/*.mp3; do
  [ -f "$track" ] || continue
  name="$(basename "${track%.mp3}")"
  ./karaoke-ass "$track" --output "results/$name" || exit "$?"
done
```

One song is processed per run. Use separate output directories for files with the same base name.

## How it works

1. FFmpeg converts the audio to 16 kHz mono for recognition.
2. A 10 ms energy envelope is calculated, and silent edges are trimmed from each window.
3. Whisper recognizes central chunks of 24 s with up to 2 s of context on each side, without carrying lyrics over from the previous window. Global rejection based on no-speech probability is disabled because it omitted parts of the reference song; silence is checked using energy.
4. Each word is assigned to a single central chunk using its midpoint. Overlapping repetitions are removed while preserving repetitions sung at different times.
5. Timings are normalized and repairs are documented. Words that cannot be placed are retained in the original recognition output and flagged.
6. If recognition covers less than 65% of a chunk's active signal, a bounded second attempt uses subchunks of up to 12 s without a prompt. Selection uses coverage and approximate confidence; this does not verify the lyrics. `--no-retry` disables this step.
7. Text is split into units, and boundaries within words are estimated near energy minima inside each word's interval.
8. Phrases are grouped and ASS is generated, preserving pauses with empty duration tags. Editing and review files are generated.

This program automates the first version we previously assembled through multiple runs and manual selection of excerpts. It contains no rules specific to that song.

## Tests

```bash
clojure -M:test
clojure -M:test lyrics
python3 -m unittest discover -s legacy/tests -p test_whisper_worker.py -v
```

Tests cover syllable splitting, text preservation, invalid timings, window ownership, repetitions, retries, checkpoint reuse, output protection, pauses, preview, offsets, conservative lyrics, and real FFmpeg decoding/regeneration. Whisper and HTTP responses are mocked in unit tests. Inference adapter tests cover CUDA recovery without loading models. Real model validation and historical migration comparisons are recorded in `VALIDACION.md`.

References: [Whisper](https://github.com/openai/whisper), [Aegisub ASS karaoke tags](https://aegisub.org/docs/latest/ass_tags/#karaoke-effect).

## Optional correction using online lyrics

```bash
./karaoke-ass vocals.wav --artist "Modern Talking" --title "Cheri Cheri Lady"
```

`--artist` and `--title` must be supplied together and cannot be empty. After Whisper completes, [LRCLIB](https://lrclib.net/docs) is queried by artist and title without an API key. This also works with `--from-json`, allowing an existing transcription to be corrected without running Whisper again. Without these arguments, no internet requests are made. Only the artist and title are sent; audio and transcription remain local.

Corrections are conservative: only words with a minimum similarity of 0.75 are replaced, between unique anchors of at least two matching words on each side. Words are neither added nor removed, and their timings do not change. Ambiguous repetitions and other differences are flagged for review. Lyrics before or after the transcribed interval are not added. LRC timings are not used.

First, `transcripcion.json` is saved, then that file is corrected using the downloaded lyrics, and the corrected JSON is loaded again to generate ASS, TXT, and TSV. `transcripcion.original.json` preserves the uncorrected input; the checkpoint also remains unchanged. `revision.json` and `ejecucion.json` record the source, identifier, status, replacements, and unresolved differences; `revision.txt` includes a summary and warnings. The result remains a draft awaiting listening review.

If the request fails, takes more than 10 seconds, returns no lyrics, or returns mismatched metadata, a warning is shown and output is generated using the original text. The catalog may not include the song or its specific version.

### Karaoke text preview

Each ASS event displays its text 200 ms before the first word, without advancing the highlighting or changing JSON, TXT, or TSV timings. At the beginning of the file, the preview is clamped to time zero. Consecutive events may overlap during those 200 ms to preserve the sung ending of the previous phrase. This preview is independent of `--offset`, which shifts the entire synchronization.
