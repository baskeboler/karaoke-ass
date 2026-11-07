import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import URLError

import numpy as np
from karaoke_ass.cli import parser, run, validate
from karaoke_ass.lyrics import fetch_lyrics, correct_words, apply_lyrics


def words(text):
    return [dict(text=t, start=i, end=i+.8, start_cs=i*100, end_cs=i*100+80,
                 language='en', probability=.8) for i, t in enumerate(text.split())]


class LyricsTests(unittest.TestCase):
    def test_cli_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory)/'audio.wav'
            audio.touch()
            for args in (['--artist', 'A'], ['--title', 'T'], ['--artist', ' ', '--title', 'T']):
                with self.assertRaises(ValueError):
                    validate(parser().parse_args([str(audio), *args]))
            for args in ([], ['--artist', ' A ', '--title', ' T ']):
                validate(parser().parse_args([str(audio), *args]))

    def test_lookup(self):
        record = dict(id=42, artistName=' Artist ', trackName='TITLE', plainLyrics='my lyrics')
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(record).encode()
        with patch('karaoke_ass.lyrics.urlopen', return_value=response) as request:
            text, source = fetch_lyrics('artist', 'title')
            self.assertEqual(text, 'my lyrics')
            self.assertEqual(source['id'], 42)
            self.assertEqual(request.call_args.kwargs['timeout'], 10)
            self.assertIn('artist_name=artist', request.call_args.args[0].full_url)
        for update in (dict(artistName='other'), dict(instrumental=True), dict(plainLyrics=None), dict(id=None)):
            response.__enter__.return_value.read.return_value = json.dumps(record | update).encode()
            with patch('karaoke_ass.lyrics.urlopen', return_value=response), self.assertRaises(ValueError):
                fetch_lyrics('artist', 'title')
        response.__enter__.return_value.read.return_value = b'invalid json'
        with patch('karaoke_ass.lyrics.urlopen', return_value=response), self.assertRaises(ValueError):
            fetch_lyrics('artist', 'title')
        response.__enter__.return_value.read.return_value = json.dumps(record | dict(
            plainLyrics=None, syncedLyrics='[ar:Artist]\n[00:01.00]hello world\n[00:02.00]next line')).encode()
        with patch('karaoke_ass.lyrics.urlopen', return_value=response):
            self.assertEqual(fetch_lyrics('artist', 'title')[0], 'hello world\nnext line')

    def test_conservative_alignment(self):
        original = words('we will survuve this night')
        corrected, changes, unresolved = correct_words(original, 'we will survive this night')
        self.assertEqual(corrected[2]['text'], 'survive')
        self.assertEqual(len(changes), 1)
        self.assertFalse(unresolved)
        self.assertEqual(original[2]['text'], 'survuve')
        for a, b in zip(original, corrected):
            self.assertEqual({k:v for k,v in a.items() if k != 'text'},
                             {k:v for k,v in b.items() if k != 'text'})
        for reference in ('we will dance this night', 'we will always survive this night',
                          'we will this night'):
            result, changes, unresolved = correct_words(original, reference)
            self.assertEqual(result, original)
            self.assertFalse(changes)
            self.assertTrue(unresolved)

    def test_repeats_and_partial_interval(self):
        original = words('we will survuve this night we will survive this night')
        self.assertFalse(correct_words(original, 'we will survive this night we will survive this night')[1])
        original = words('we will survive this night')
        self.assertEqual(correct_words(original, 'intro words we will survive this night final verse')[2], [])
        self.assertEqual(correct_words(original, 'We will survive this night!')[1], [])

    def test_network_failure(self):
        for failure in (URLError('offline'), TimeoutError('timeout'), ValueError('invalid')):
            issues = []
            original = words('we will survive this night')
            with patch('karaoke_ass.lyrics.fetch_lyrics', side_effect=failure):
                result, report = apply_lyrics(original, 'A', 'T', issues)
            self.assertEqual(result, original)
            self.assertEqual(report['status'], 'unavailable')
            self.assertEqual(issues[0]['code'], 'lyrics_unavailable')

    def test_after_whisper(self):
        original = dict(words=[{k:v for k,v in w.items() if k not in ('start_cs', 'end_cs')}
                               for w in words('we will survuve this night')])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root/'audio.wav'
            audio.touch()
            output = root/'out'
            options = parser().parse_args([str(audio), '-o', str(output), '--artist', 'A', '--title', 'T'])
            completed = []
            def recognize(*args):
                completed.append(True)
                return original
            def lookup(*args):
                self.assertTrue(completed)
                before = json.loads((output/'transcripcion.json').read_text())
                self.assertEqual(before['words'][2]['text'], 'survuve')
                self.assertFalse((output/'karaoke.ass').exists())
                return 'we will survive this night', dict(provider='LRCLIB', id=1, url='https://lrclib.net/api/get/1')
            from karaoke_ass.timing import timed_units
            def check_json(render_words, *args):
                saved = json.loads((output/'transcripcion.json').read_text())
                self.assertEqual(saved['words'][2]['text'], 'survive')
                self.assertEqual([w['text'] for w in render_words], [w['text'] for w in saved['words']])
                return timed_units(render_words, *args)
            with patch('karaoke_ass.timing.timed_units', side_effect=check_json), patch('karaoke_ass.audio.load_audio', return_value=np.ones(6*16000, dtype=np.float32)), patch(
                    'karaoke_ass.recognition.recognize', side_effect=recognize), patch(
                    'karaoke_ass.lyrics.fetch_lyrics', side_effect=lookup):
                self.assertEqual(run(options), 0)
            saved = json.loads((output/'transcripcion.json').read_text())
            self.assertEqual(saved['words'][2]['text'], 'survive')
            self.assertEqual(original['words'][2]['text'], 'survuve')

    def test_pipeline(self):
        source = {'provider': 'LRCLIB', 'id': 42, 'url': 'https://lrclib.net/api/get/42'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root/'audio.wav'
            audio.touch()
            original = dict(words=[{k:v for k,v in w.items() if k not in ('start_cs', 'end_cs')}
                                   for w in words('we will survuve this night')])
            input_json = root/'input.json'
            input_json.write_text(json.dumps(original))
            common = [str(audio), '--from-json', str(input_json), '--units', 'word']
            for enabled, failure in ((False, False), (True, False), (True, True)):
                output = root/f'{enabled}-{failure}'
                args = common + ['-o', str(output)]
                if enabled:
                    args += ['--artist', 'Artist', '--title', 'Title']
                with patch('karaoke_ass.audio.load_audio', return_value=np.ones(6*16000, dtype=np.float32)), patch(
                        'karaoke_ass.lyrics.fetch_lyrics', return_value=('we will survive this night', source),
                        side_effect=URLError('offline') if failure else None) as fetch:
                    self.assertEqual(run(parser().parse_args(args)), 0)
                self.assertEqual(fetch.call_count, int(enabled))
                expected = 'survive' if enabled and not failure else 'survuve'
                normalized = json.loads((output/'transcripcion.json').read_text())
                self.assertEqual(normalized['words'][2]['text'], expected)
                self.assertEqual(normalized['words'][2]['start'], 2)
                saved = json.loads((output/'transcripcion.original.json').read_text())
                self.assertEqual(saved['words'], original['words'])
                for name in ('karaoke.ass', 'transcripcion.txt', 'unidades.tsv'):
                    self.assertIn(expected, (output/name).read_text(encoding='utf-8-sig'))
                for name in ('revision.json', 'ejecucion.json'):
                    report = json.loads((output/name).read_text())
                    self.assertEqual('lyrics' in report, enabled)
                    if enabled:
                        self.assertEqual(report['lyrics']['status'], 'unavailable' if failure else 'corrected')
                if enabled and not failure:
                    self.assertIn(source['url'], (output/'revision.txt').read_text())


if __name__ == '__main__':
    unittest.main()
