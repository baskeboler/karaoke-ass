"""JSON-lines inference adapter. Application orchestration lives in Clojure.

Stdout is exclusively the protocol; model/library output goes to stderr.
This adapter contains only inference; the application runs in Clojure.
"""
import contextlib
import gc
import json
import sys


class WhisperRuntime:
    def __init__(self, torch, whisper, options, device):
        self.torch, self.whisper, self.options = torch, whisper, options
        self.device, self.model, self.fell_back_to_cpu = device, None, False

    def _transcribe(self, audio, prompt):
        if self.model is None:
            print(f'Loading Whisper {self.options.model} ({self.device})', file=sys.stderr, flush=True)
            self.model = self.whisper.load_model(self.options.model, device=self.device,
                                                 download_root=self.options.model_dir)
        kwargs = dict(language=None if self.options.language == 'auto' else self.options.language,
                      task='transcribe', word_timestamps=True, fp16=self.device == 'cuda',
                      verbose=None, condition_on_previous_text=False, no_speech_threshold=None,
                      temperature=0.0)
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
                raise ValueError('CUDA sin memoria; usa --device cpu o --model small --device cuda. '
                                 'Los tramos completados siguen guardados.') from None
        # Outside the handler: tracebacks must release partially allocated models.
        self.model = None
        gc.collect()
        self.torch.cuda.empty_cache()
        self.device, self.fell_back_to_cpu = 'cpu', True
        print('CUDA sin memoria; continuando en CPU con el mismo modelo.', file=sys.stderr, flush=True)
        return self._transcribe(audio, prompt)


def serve():
    from types import SimpleNamespace
    import numpy as np
    import torch
    import whisper
    from whisper.tokenizer import LANGUAGES

    config = json.loads(sys.stdin.readline())
    options = SimpleNamespace(**config)
    if options.language != 'auto' and options.language not in LANGUAGES:
        raise ValueError('Usa un código de idioma Whisper (es, en…) o auto.')
    torch.set_num_threads(options.threads)
    device = ('cuda' if torch.cuda.is_available() else 'cpu') if options.device == 'auto' else options.device
    if device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA no está disponible. Usa --device cpu o --device auto.')
    runtime = WhisperRuntime(torch, whisper, options, device)
    samples = np.memmap(options.pcm, dtype='<f4', mode='r')
    print(json.dumps(dict(version=whisper.__version__, device=device)), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        with contextlib.redirect_stdout(sys.stderr):
            result = runtime.transcribe(np.array(samples[request['lo']:request['hi']]), request.get('prompt'))
        print(json.dumps(dict(result=result, device=runtime.device,
                              cuda_oom_fallback=runtime.fell_back_to_cpu), allow_nan=False), flush=True)


if __name__ == '__main__':
    try:
        serve()
    except Exception as exc:
        print(json.dumps(dict(error=f'{type(exc).__name__}: {exc}')), flush=True)
        sys.exit(2)
