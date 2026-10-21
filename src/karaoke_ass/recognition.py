"""Windowed Whisper recognition with source-time ownership and resumable checkpoints."""
import hashlib
import gc
import json
import math
from pathlib import Path
import sys
import unicodedata
import numpy as np
from .audio import RATE, active_slice


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def coverage(result, offset, mask, core_start, core_end):
    lo, hi = round(core_start*100), min(len(mask), round(core_end*100))
    active = mask[lo:hi]
    covered = np.zeros(len(active), dtype=bool)
    probabilities = []
    for segment in result.get('segments', []):
        for word in segment.get('words', []):
            a, b = word['start']+offset, word['end']+offset
            # A single hallucinated word stretching over a whole phrase must not
            # count as good coverage. Long held notes remain reviewable.
            b = min(b, a+3)
            x, y = max(0,round(a*100)-lo-10), min(len(active),round(b*100)-lo+10)
            if y>x:
                covered[x:y] = True
                probabilities.append(word.get('probability', .5))
    ratio = float(np.count_nonzero(active & covered))/max(1,np.count_nonzero(active))
    confidence = float(np.mean(probabilities)) if probabilities else 0
    return ratio, confidence, np.count_nonzero(active)/100


class WhisperRuntime:
    """Keep the requested model; retry once on CPU after an automatic CUDA OOM."""

    def __init__(self, torch, whisper, options, device):
        self.torch = torch
        self.whisper = whisper
        self.options = options
        self.device = device
        self.model = None
        self.fell_back_to_cpu = False

    def _transcribe(self, audio, prompt):
        if self.model is None:
            print(f'Cargando Whisper {self.options.model} ({self.device})…', file=sys.stderr, flush=True)
            self.model = self.whisper.load_model(
                self.options.model, device=self.device, download_root=self.options.model_dir,
            )
        kwargs = dict(
            language=None if self.options.language == 'auto' else self.options.language,
            task='transcribe', word_timestamps=True, fp16=self.device == 'cuda', verbose=None,
            condition_on_previous_text=False, no_speech_threshold=None, temperature=0.0,
        )
        if prompt is not None:
            kwargs['initial_prompt'] = prompt
        return self.model.transcribe(audio, **kwargs)

    def transcribe(self, audio, prompt=None):
        try:
            return self._transcribe(audio, prompt)
        except self.torch.cuda.OutOfMemoryError:
            if self.device != 'cuda':
                raise
            if self.options.device != 'auto':
                raise ValueError(
                    f'CUDA no tiene memoria suficiente para Whisper {self.options.model}. '
                    'Ejecuta de nuevo con --device cpu, o prueba --model small --device cuda. '
                    'Los tramos completados siguen guardados en el checkpoint.'
                ) from None
        # Leave the exception handler first: its traceback can retain tensors,
        # including a partially loaded model that was never assigned to self.model.
        self.model = None
        gc.collect()
        self.torch.cuda.empty_cache()
        self.device = 'cpu'
        self.fell_back_to_cpu = True
        print(
            'CUDA sin memoria suficiente; continuando en CPU con el mismo modelo. '
            'Se conservan los tramos ya completados.',
            file=sys.stderr, flush=True,
        )
        return self._transcribe(audio, prompt)


def recognize(audio, mask, options, checkpoint: Path, source_hash: str) -> dict:
    try:
        import torch
        import whisper
    except ImportError as exc:
        raise ValueError('Falta Whisper. Instala el proyecto con: pip install ".[speech]"') from exc
    torch.set_num_threads(options.threads)
    device = ('cuda' if torch.cuda.is_available() else 'cpu') if options.device == 'auto' else options.device
    if device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA no está disponible. Usa --device cpu o --device auto.')
    duration = len(audio)/RATE
    start, end = options.start, min(options.end if options.end is not None else duration, duration)
    if end <= start:
        raise ValueError('El intervalo solicitado no contiene audio.')
    signature = {
        'schema': 1, 'sha256': source_hash, 'model': options.model, 'language': options.language,
        'prompt': options.prompt, 'chunk_seconds': options.chunk_seconds, 'context': 2,
        'start': start, 'end': end, 'whisper': whisper.__version__,
        'temperature': 0, 'no_speech_threshold': None, 'retry_enabled': not options.no_retry,
    }
    cached = {}
    if checkpoint.exists() and not options.no_resume:
        previous = json.loads(checkpoint.read_text(encoding='utf-8'))
        previous_signature = previous.get('signature', {})
        # Older checkpoints contain the same primary recognition parameters;
        # they can be reused and evaluated by the new retry stage.
        previous_signature.setdefault('retry_enabled', True)
        if previous_signature == signature:
            cached = previous.get('windows', {})
            print(f'Reanudando: {len(cached)} tramos guardados.', file=sys.stderr, flush=True)
    count = math.ceil((end-start)/options.chunk_seconds)
    runtime = WhisperRuntime(torch, whisper, options, device)
    all_segments, windows = [], []
    for index in range(count):
        core_start = start + index*options.chunk_seconds
        core_end = min(end, core_start+options.chunk_seconds)
        lo = round(max(start, core_start-2)*RATE)
        hi = round(min(end, core_end+2)*RATE)
        bounds = active_slice(mask, lo, hi)
        print(f'Tramo {index+1}/{count}: {core_start:.1f}–{core_end:.1f} s ({runtime.device})', file=sys.stderr, flush=True)
        if bounds is None:
            windows.append({'start': core_start, 'end': core_end, 'language': None, 'silent': True})
            continue
        lo, hi = bounds
        cache_key = str(index)
        if cache_key in cached:
            result = cached[cache_key]
        else:
            result = runtime.transcribe(audio[lo:hi], prompt=options.prompt or None)
            cached[cache_key] = result
            atomic_json(checkpoint, {'signature': signature, 'windows': cached})
        primary_coverage, primary_confidence, active_seconds = coverage(result, lo/RATE, mask, core_start, core_end)
        if (not options.no_retry and active_seconds >= 1 and primary_coverage < .65
                and not result.get('_retry_checked')):
            print(f'  Cobertura baja ({primary_coverage:.0%}); segundo intento por subtramos sin prompt…', file=sys.stderr, flush=True)
            retry_segments = []
            # Smaller, independently decoded windows reduce cross-language and
            # prompt bias. At most one retry pass per core is allowed.
            sub_start = core_start
            while sub_start < core_end:
                sub_end = min(core_end, sub_start+12)
                sub_lo = round(max(start, sub_start-.5)*RATE)
                sub_hi = round(min(end, sub_end+.5)*RATE)
                sub_bounds = active_slice(mask, sub_lo, sub_hi)
                if sub_bounds is not None:
                    sub_lo, sub_hi = sub_bounds
                    alternative = runtime.transcribe(audio[sub_lo:sub_hi])
                    for segment in alternative.get('segments', []):
                        kept_retry = []
                        for word in segment.get('words', []):
                            a, b = word['start']+sub_lo/RATE, word['end']+sub_lo/RATE
                            if sub_start <= (a+b)/2 < sub_end:
                                kept_retry.append(dict(word,start=a-lo/RATE,end=b-lo/RATE,
                                                       language=alternative.get('language')))
                        if kept_retry:
                            retry_segments.append(dict(segment,words=kept_retry,
                                                       language=alternative.get('language'),
                                                       start=kept_retry[0]['start'],end=kept_retry[-1]['end']))
                sub_start = sub_end
            retry = {'language': result.get('language'), 'segments':retry_segments}
            retry_coverage, retry_confidence, _ = coverage(retry,lo/RATE,mask,core_start,core_end)
            # An isolated hallucinated proper noun can have high confidence.
            # Coverage therefore has more weight than model confidence here.
            use_retry = (retry_coverage > primary_coverage+.1 and retry_confidence >= .2
                         and .75*retry_coverage+.25*retry_confidence > .75*primary_coverage+.25*primary_confidence+.05)
            diagnostic = {'primary_coverage':primary_coverage,'retry_coverage':retry_coverage,
                          'selected':'retry' if use_retry else 'primary'}
            result = dict(retry, _alternative=result) if use_retry else dict(result, _alternative=retry)
            result['_retry_checked'] = True
            result['_retry_diagnostic'] = diagnostic
            cached[cache_key] = result
            atomic_json(checkpoint, {'signature': signature, 'windows': cached})
        language = result.get('language', options.language)
        windows.append({'start': core_start, 'end': core_end, 'language': language, 'silent': False,
                        'retry':result.get('_retry_diagnostic'), 'coverage':coverage(result,lo/RATE,mask,core_start,core_end)[0]})
        # Each word belongs to the core containing its midpoint. Context is never
        # concatenated blindly: that would duplicate lyrics at every boundary.
        for segment in result.get('segments', []):
            kept = []
            for word in segment.get('words', []):
                a, b = float(word['start'])+lo/RATE, float(word['end'])+lo/RATE
                midpoint = (a+b)/2
                if core_start <= midpoint < core_end or (index == count-1 and midpoint == end):
                    kept.append(dict(word, start=a, end=b, language=word.get('language') or segment.get('language') or language))
            if kept:
                all_segments.append(dict(segment, words=kept, language=language,
                                         start=kept[0]['start'], end=kept[-1]['end']))
    return {'schema_version': 1, 'source_sha256': source_hash, 'language': options.language,
            'model': options.model, 'windows': windows, 'segments': all_segments,
            'device': runtime.device, 'cuda_oom_fallback': runtime.fell_back_to_cpu,
            'interval': [start, end]}


def words_from_json(data: dict, fallback_language: str = 'es') -> list[dict]:
    if not isinstance(data, dict):
        raise ValueError('La transcripción debe ser un objeto JSON.')
    if isinstance(data.get('words'), list):
        sources = [(data, data['words'])]
    elif isinstance(data.get('segments'), list):
        if not all(isinstance(segment, dict) for segment in data['segments']):
            raise ValueError('Cada segmento del JSON debe ser un objeto.')
        sources = [(segment, segment.get('words', [])) for segment in data['segments']]
    else:
        raise ValueError('El JSON debe contener «words» o «segments» con tiempos de palabra.')
    words = []
    for segment, items in sources:
        if not isinstance(items, list):
            raise ValueError('El campo words debe ser una lista.')
        language = segment.get('language') or data.get('language') or fallback_language
        if not isinstance(language, str):
            raise ValueError('El idioma debe ser una cadena de texto.')
        if language in ('auto', 'es/en'):
            language = fallback_language
        if segment.get('text', '').strip() and not items:
            # Empty segments from Whisper must be visible to the caller.
            a, b = segment.get('start', 0), segment.get('end', 0)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, b)):
                raise ValueError('Tiempos no válidos en un segmento sin palabras.')
            words.append({'text': '', 'missing_segment': True, 'start': a, 'end': b})
        for word in items:
            if not isinstance(word, dict):
                raise ValueError('Cada palabra del JSON debe ser un objeto.')
            text = word.get('text', word.get('word'))
            if not isinstance(text, str):
                raise ValueError('Falta el texto de una palabra en el JSON.')
            for field in ('start', 'end'):
                value = word.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f'Tiempo {field} no válido para {text!r}.')
            probability = word.get('probability')
            if probability is not None and (not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1):
                raise ValueError(f'Probabilidad no válida para {text!r}.')
            if not isinstance(word.get('language') or language, str):
                raise ValueError('El idioma de cada palabra debe ser una cadena de texto.')
            words.append({'text': unicodedata.normalize('NFC', text.strip()), 'start': word['start'], 'end': word['end'],
                          'language': word.get('language') or language, 'probability': probability})
    return words
