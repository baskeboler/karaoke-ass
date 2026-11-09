"""Inference adapter regression checks; no installed Whisper or GPU required."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import weakref

spec = importlib.util.spec_from_file_location('whisper_worker', Path(__file__).parents[1] / 'whisper_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class InferenceAdapterTests(unittest.TestCase):
    class OOM(RuntimeError):
        pass

    def runtime(self, load, requested='auto', actual='cuda'):
        self.cleanups = []
        torch = SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=self.OOM,
                                                      empty_cache=lambda: self.cleanups.append(True)))
        options = SimpleNamespace(model='medium', model_dir=None, language='auto', device=requested)
        return worker.WhisperRuntime(torch, SimpleNamespace(load_model=load), options, actual)

    def test_load_oom_frees_traceback_before_cpu_retry(self):
        calls, refs = [], []
        class Allocation:
            pass
        def load(name, device, download_root):
            calls.append((name, device))
            if device == 'cuda':
                allocation = Allocation()
                refs.append(weakref.ref(allocation))
                raise self.OOM('oom')
            self.assertIsNone(refs[0]())
            return SimpleNamespace(transcribe=lambda audio, **kwargs: kwargs)
        runtime = self.runtime(load)
        result = runtime.transcribe([], prompt='names')
        self.assertEqual(calls, [('medium', 'cuda'), ('medium', 'cpu')])
        self.assertFalse(result['fp16'])
        self.assertEqual(result['initial_prompt'], 'names')
        self.assertFalse(result['condition_on_previous_text'])
        self.assertIsNone(result['no_speech_threshold'])
        self.assertTrue(runtime.fell_back_to_cpu)
        self.assertEqual(self.cleanups, [True])
        runtime.transcribe([])
        self.assertEqual(len(calls), 2)

    def test_transcribe_oom_releases_gpu_model(self):
        refs, calls = [], []
        class Model:
            def __init__(model, device):
                model.device = device
            def transcribe(model, audio, **kwargs):
                calls.append(model.device)
                if model.device == 'cuda':
                    raise self.OOM('oom')
                return {}
        def load(name, device, download_root):
            if device == 'cpu':
                self.assertIsNone(refs[0]())
            model = Model(device)
            if device == 'cuda':
                refs.append(weakref.ref(model))
            return model
        runtime = self.runtime(load)
        runtime.transcribe([])
        runtime.transcribe([])
        self.assertEqual(calls, ['cuda', 'cpu', 'cpu'])

    def test_explicit_cuda_and_unrelated_errors_are_strict(self):
        for requested, actual, error in [('cuda', 'cuda', self.OOM('oom')),
                                          ('auto', 'cpu', self.OOM('cpu oom')),
                                          ('auto', 'cuda', RuntimeError('unrelated CUDA error'))]:
            calls = []
            def load(*args, **kwargs):
                calls.append(True)
                raise error
            runtime = self.runtime(load, requested, actual)
            with self.assertRaises(ValueError if requested == 'cuda' else type(error)):
                runtime.transcribe([])
            self.assertEqual(len(calls), 1)
            self.assertFalse(runtime.fell_back_to_cpu)


if __name__ == '__main__':
    unittest.main()
