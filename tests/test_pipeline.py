import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref
import numpy as np
from karaoke_ass.audio import envelope, activity
from karaoke_ass.cli import parser, run, output_lock
from karaoke_ass.recognition import words_from_json, recognize, WhisperRuntime
from karaoke_ass.render import render, safe_text
from karaoke_ass.syllables import split, validate_overrides
from karaoke_ass.timing import normalize, boundaries, timed_units


def word(text,start,end,language='es',probability=.9):
    return dict(text=text,start=start,end=end,language=language,probability=probability)


def cs(text):
    h,m,s=text.split(':')
    return round((int(h)*3600+int(m)*60+float(s))*100)


def assert_ass(test,ass):
    last=0
    for line in ass.splitlines():
        if not line.startswith('Dialogue:'):
            continue
        f=line.split(',',9);a,b=cs(f[1]),cs(f[2])
        test.assertGreaterEqual(a,last)
        test.assertGreater(b,a)
        test.assertEqual(sum(map(int,re.findall(r'\\k(?:f|o)?(\d+)',f[9]))),b-a)
        last=b


class SyllablesTests(unittest.TestCase):
    def test_spanish_digraphs_hiatus_diphthongs(self):
        expected={'paquete':['pa','que','te'],'guitarra':['gui','ta','rra'],
                  'canción':['can','ción'],'tierra':['tie','rra'],'día':['dí','a'],
                  'ahora':['a','ho','ra'],'poeta':['po','e','ta'],'buey':['buey'],
                  'prohibir':['prohi','bir'],'país':['pa','ís']}
        for text,parts in expected.items():
            with self.subTest(text=text):self.assertEqual(split(text,'es'),parts)

    def test_punctuation_capitalization_and_override(self):
        overrides=validate_overrides({'Spotify':['Spo','ti','fy']})
        self.assertEqual(split('¡SPOTIFY!','es',overrides),['¡SPO','TI','FY!'])
        self.assertEqual(''.join(split('“corazón,”','es')),'“corazón,”')
        for invalid in ({'hola':['ho','lo']},{'x':[]},{'x':['']},[]):
            with self.assertRaises(ValueError):validate_overrides(invalid)

    def test_english_and_unsupported(self):
        self.assertEqual(split('baby','en'),['ba','by'])
        self.assertEqual(split('make','en'),['make'])
        self.assertEqual(split("don't",'en'),["don't"])
        self.assertEqual(split('bonjour','fr'),['bonjour'])


class TimingTests(unittest.TestCase):
    def test_duplicate_only_when_time_overlaps(self):
        issues=[]
        words=normalize([word('sí',0,.4),word('sí',.02,.42),word('sí',.5,.9)],1,issues)
        self.assertEqual(len(words),2)
        self.assertIn('duplicate',[i['code'] for i in issues])

    def test_zero_width_does_not_fill_a_silence(self):
        issues=[]
        words=normalize([word('uno',0,.5),word('dos',2,2)],3,issues)
        self.assertEqual(len(words),1)
        self.assertEqual(words[0]['end_cs'],50)

    def test_overlap_and_zero_width_are_reported(self):
        issues=[]
        words=normalize([word('mi',0,.8),word('voz',.8,.8),word('canta',.75,1.3)],2,issues)
        self.assertEqual(len(words),3)
        self.assertTrue(any(i['code']=='timing_repaired' for i in issues))
        for a,b in zip(words,words[1:]):self.assertLessEqual(a['end_cs'],b['start_cs'])

    def test_random_timelines_preserve_invariants(self):
        random.seed(29)
        for _ in range(100):
            original=[word(str(i),round(random.random()*5,2),0) for i in range(50)]
            for w in original:w['end']=min(5,w['start']+random.choice([0,.01,.4,1]))
            words=normalize(original,5,[])
            for w in words:self.assertTrue(0<=w['start_cs']<w['end_cs']<=500,w)
            for a,b in zip(words,words[1:]):self.assertLessEqual(a['end_cs'],b['start_cs'])

    def test_boundary_quantization(self):
        for width in (3,4,10,101):
            for n in range(1,min(5,width)+1):
                cuts=boundaries(2,2+width,n,np.ones(200))
                self.assertEqual((cuts[0],cuts[-1]),(2,2+width))
                self.assertTrue(all(a<b for a,b in zip(cuts,cuts[1:])))

    def test_invalid_input(self):
        for value in (float('nan'),float('inf'),None,'1'):
            with self.assertRaises(ValueError):words_from_json({'words':[{'text':'hola','start':value,'end':2}]})
        with self.assertRaises(ValueError):normalize([word('hola',2,1)],3,[])
        with self.assertRaises(ValueError):words_from_json({'segments':[{'words':[{}]}]})


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.options=parser().parse_args(['unused.wav'])
        self.options.overrides={}
        self.words=normalize([word('Mi',.1,.3),word('corazón',.6,1.6),word('canta.',1.8,2.4)],3,[])
        self.units=timed_units(self.words,np.ones(300),{},'syllable',[])

    def test_all_effects_and_offsets(self):
        for effect in ('k','kf','ko'):
            for offset in (-.65,0,.19):
                self.options.effect=effect;self.options.offset=offset
                ass,rows,transcript=render(self.units,self.options)
                assert_ass(self,ass)
                self.assertTrue(rows)
                self.assertTrue(all(r['inicio_s']<r['fin_s'] for r in rows))
                self.assertTrue(transcript)

    def test_override_injection_is_plain_text(self):
        self.assertEqual(safe_text(r'{\pos(0,0)}Hi\Nbye'), '｛＼pos(0,0)｝Hi＼Nbye')

    def test_silence_is_accounted_for(self):
        ass,_,_=render(self.units,self.options)
        self.assertIn(r'{\k30}',ass)
        assert_ass(self,ass)


class RecognitionTests(unittest.TestCase):
    def test_chunk_ownership_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint=Path(tmp)/'checkpoint.json'
            options=parser().parse_args(['unused.wav','--chunk-seconds','5','--device','cpu','--no-retry'])
            calls=[]
            class Model:
                def transcribe(self,audio,**kwargs):
                    calls.append(kwargs)
                    offset=float(audio[0])
                    # Both context windows contain both repeated words.
                    return {'language':'en','segments':[{'words':[
                        {'word':' hi','start':4.8-offset,'end':5.2-offset},
                        {'word':' hi','start':5.4-offset,'end':5.8-offset}]}]}
            whisper=SimpleNamespace(__version__='test',load_model=lambda *a,**kw:Model())
            torch=SimpleNamespace(set_num_threads=lambda n:None,cuda=SimpleNamespace(is_available=lambda:False))
            audio=np.arange(160000,dtype=np.float32)/16000
            with patch.dict(sys.modules,{'whisper':whisper,'torch':torch}):
                data=recognize(audio,np.ones(1000,dtype=bool),options,checkpoint,'abc')
                self.assertEqual(len(words_from_json(data,'en')),2)
                self.assertEqual(len(calls),2)
                self.assertIsNone(calls[0]['no_speech_threshold'])
                self.assertFalse(calls[0]['condition_on_previous_text'])
                recognize(audio,np.ones(1000,dtype=bool),options,checkpoint,'abc')
                self.assertEqual(len(calls),2)
                options.prompt='changed'
                recognize(audio,np.ones(1000,dtype=bool),options,checkpoint,'abc')
                self.assertEqual(len(calls),4)

    def test_retry_recovers_low_coverage_without_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            options=parser().parse_args(['unused.wav','--chunk-seconds','5','--device','cpu','--prompt','hint'])
            calls=[]
            class Model:
                def transcribe(self,audio,**kwargs):
                    calls.append(kwargs)
                    if len(calls)==1:return {'language':'es','segments':[]}
                    return {'language':'es','segments':[{'words':[
                        {'word':' canción','start':.1,'end':len(audio)/16000-.1,'probability':.95}]}]}
            whisper=SimpleNamespace(__version__='test',load_model=lambda *a,**kw:Model())
            torch=SimpleNamespace(set_num_threads=lambda n:None,cuda=SimpleNamespace(is_available=lambda:False))
            with patch.dict(sys.modules,{'whisper':whisper,'torch':torch}):
                data=recognize(np.ones(80000,dtype=np.float32),np.ones(500,dtype=bool),options,Path(tmp)/'cache.json','abc')
            self.assertEqual(len(calls),2)
            self.assertNotIn('initial_prompt',calls[1])
            self.assertEqual(data['windows'][0]['retry']['selected'],'retry')

    def test_short_envelope_and_silence(self):
        energy=envelope(np.zeros(1,dtype=np.float32))
        self.assertEqual(len(energy),1)
        self.assertFalse(activity(energy).any())


class CudaRecoveryTests(unittest.TestCase):
    class OOM(RuntimeError):
        pass

    def runtime(self, loader, device='auto'):
        options=parser().parse_args(['unused.wav','--device',device])
        self.cleanups=[]
        cuda=SimpleNamespace(OutOfMemoryError=self.OOM,empty_cache=lambda:self.cleanups.append('cleared'),
                             is_available=lambda:True)
        torch=SimpleNamespace(cuda=cuda,set_num_threads=lambda n:None)
        whisper=SimpleNamespace(load_model=loader,__version__='test')
        self.modules={'torch':torch,'whisper':whisper}
        return WhisperRuntime(torch,whisper,options,'cuda' if device!='cpu' else 'cpu')

    def test_auto_recovers_from_load_oom_and_frees_failed_frame(self):
        loads=[];allocation_refs=[]
        class Allocation:pass
        def load(name,device,download_root):
            loads.append((name,device))
            if device=='cuda':
                allocation=Allocation()
                allocation_refs.append(weakref.ref(allocation))
                raise self.OOM('CUDA out of memory')
            self.assertIsNone(allocation_refs[0]())
            return SimpleNamespace(transcribe=lambda audio,**kwargs:kwargs)
        runtime=self.runtime(load)
        result=runtime.transcribe(np.ones(10),prompt='names')
        self.assertEqual(loads,[('medium','cuda'),('medium','cpu')])
        self.assertFalse(result['fp16'])
        self.assertEqual(result['initial_prompt'],'names')
        self.assertEqual(self.cleanups,['cleared'])
        self.assertTrue(runtime.fell_back_to_cpu)
        runtime.transcribe(np.ones(10))
        self.assertEqual(len(loads),2)  # CPU remains active for later chunks.

    def test_auto_recovers_from_transcribe_oom_and_releases_model(self):
        calls=[];gpu_refs=[]
        class Model:
            def __init__(model,device):model.device=device
            def transcribe(model,audio,**kwargs):
                calls.append((model.device,kwargs['fp16']))
                if model.device=='cuda':raise self.OOM('out of memory')
                return {'language':'es','segments':[]}
        def load(name,device,download_root):
            if device=='cpu':self.assertIsNone(gpu_refs[0]())
            model=Model(device)
            if device=='cuda':gpu_refs.append(weakref.ref(model))
            return model
        runtime=self.runtime(load)
        runtime.transcribe(np.ones(10))
        self.assertEqual(calls,[('cuda',True),('cpu',False)])

    def test_explicit_cuda_does_not_silently_switch_device(self):
        loads=[]
        def load(name,device,download_root):
            loads.append(device)
            raise self.OOM('out of memory')
        runtime=self.runtime(load,device='cuda')
        with self.assertRaisesRegex(ValueError,'--device cpu'):
            runtime.transcribe(np.ones(10))
        self.assertEqual(loads,['cuda'])
        self.assertFalse(runtime.fell_back_to_cpu)

    def test_cpu_oom_and_unrelated_runtime_errors_propagate(self):
        for device,error in [('cpu',self.OOM('CPU out of memory')),('auto',RuntimeError('unrelated CUDA error'))]:
            calls=[]
            def load(name,device,download_root):
                calls.append(device)
                raise error
            runtime=self.runtime(load,device=device)
            with self.assertRaises(type(error)):
                runtime.transcribe(np.ones(10))
            self.assertEqual(len(calls),1)
            self.assertFalse(runtime.fell_back_to_cpu)

    def test_completed_chunks_survive_transcribe_oom_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint=Path(tmp)/'cache.json';calls=[]
            class Model:
                def __init__(model,device):model.device=device
                def transcribe(model,audio,**kwargs):
                    calls.append(model.device)
                    if model.device=='cuda' and len(calls)==2:raise self.OOM('out of memory')
                    offset=float(audio[0])
                    absolute=1 if offset<1 else 6
                    return {'language':'es','segments':[{'words':[
                        {'word':' voz','start':absolute-offset,'end':absolute-offset+.5}]}]}
            runtime=self.runtime(lambda name,device,download_root:Model(device))
            options=parser().parse_args(['unused.wav','--chunk-seconds','5','--no-retry'])
            audio=np.arange(160000,dtype=np.float32)/16000
            with patch.dict(sys.modules,self.modules):
                result=recognize(audio,np.ones(1000,dtype=bool),options,checkpoint,'hash')
                self.assertEqual(calls,['cuda','cuda','cpu'])
                self.assertEqual(len(words_from_json(result)),2)
                self.assertTrue(result['cuda_oom_fallback'])
                self.assertEqual(result['device'],'cpu')
                self.assertEqual(len(json.loads(checkpoint.read_text())['windows']),2)
                options.device='cpu'
                recognize(audio,np.ones(1000,dtype=bool),options,checkpoint,'hash')
                self.assertEqual(len(calls),3)

    def test_low_coverage_retry_also_recovers_from_cuda_oom(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            class Model:
                def __init__(model,device):model.device=device
                def transcribe(model,audio,**kwargs):
                    calls.append((model.device,kwargs))
                    if len(calls)==1:return {'language':'es','segments':[]}
                    if model.device=='cuda':raise self.OOM('out of memory')
                    return {'language':'es','segments':[{'words':[
                        {'word':' canción','start':.1,'end':len(audio)/16000-.1,'probability':.95}]}]}
            self.runtime(lambda name,device,download_root:Model(device))
            options=parser().parse_args(['unused.wav','--chunk-seconds','5'])
            with patch.dict(sys.modules,self.modules):
                result=recognize(np.ones(80000,dtype=np.float32),np.ones(500,dtype=bool),options,Path(tmp)/'cache.json','hash')
            self.assertEqual([c[0] for c in calls],['cuda','cuda','cpu'])
            self.assertNotIn('initial_prompt',calls[-1][1])
            self.assertTrue(result['cuda_oom_fallback'])
            self.assertEqual(result['windows'][0]['retry']['selected'],'retry')


class CliIntegrationTests(unittest.TestCase):
    def test_json_to_files_real_audio_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);audio=tmp/'audio with spaces.wav';source=tmp/'words.json'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=220:duration=3','-y',str(audio)],check=True)
            source.write_text(json.dumps({'language':'es','words':[word('Hola,',.2,.8),word('canción.',1,2)]}))
            options=parser().parse_args([str(audio),'--from-json',str(source),'-o',str(tmp/'out')])
            self.assertEqual(run(options),0)
            assert_ass(self,(tmp/'out/karaoke.ass').read_text(encoding='utf-8-sig'))
            report=json.loads((tmp/'out/revision.json').read_text())
            self.assertFalse(report['verified_by_listening'])
            with self.assertRaises(ValueError):run(options)
            # A normalized result can be used to regenerate into another folder.
            options.from_json=tmp/'out/transcripcion.json';options.output=tmp/'corrected'
            self.assertEqual(run(options),0)
            self.assertEqual((tmp/'out/karaoke.ass').read_bytes(),(tmp/'corrected/karaoke.ass').read_bytes())

    def test_output_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            with output_lock(Path(tmp)):
                with self.assertRaises(ValueError):
                    with output_lock(Path(tmp)):pass

    def test_input_in_output_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);(tmp/'karaoke.ass').touch()
            options=parser().parse_args([str(tmp/'karaoke.ass'),'-o',str(tmp),'--overwrite'])
            with self.assertRaisesRegex(ValueError,'entrada'):run(options)


if __name__=='__main__':unittest.main()
