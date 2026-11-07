"""Optional lyric lookup and conservative, timestamp-preserving corrections."""
from difflib import SequenceMatcher
from http.client import HTTPException
import json
import re
import unicodedata
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import __version__
from .timing import notice


def comparison(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def metadata_key(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def fetch_lyrics(artist, title):
    """Fetch one matching record; never send audio or transcription text."""
    url = 'https://lrclib.net/api/get?' + urlencode({'artist_name': artist, 'track_name': title})
    request = Request(url, headers={'User-Agent': f'karaoke-ass/{__version__}', 'Accept': 'application/json'})
    with urlopen(request, timeout=10) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError('Respuesta de letras demasiado grande.')
    record = json.loads(payload)
    if not isinstance(record, dict):
        raise ValueError('Respuesta de letras inválida.')
    for field, expected in (('artistName', artist), ('trackName', title)):
        if not isinstance(record.get(field), str) or metadata_key(record[field]) != metadata_key(expected):
            raise ValueError('Artista o título de la letra no coincide.')
    if record.get('instrumental'):
        raise ValueError('El registro está marcado como instrumental.')
    text = record.get('plainLyrics')
    if not isinstance(text, str) or not text.strip():
        synced = record.get('syncedLyrics')
        if not isinstance(synced, str):
            raise ValueError('No hay letra disponible.')
        lines = []
        for line in synced.splitlines():
            if re.match(r'^\s*\[\d+:\d+(?:\.\d+)?\]', line):
                lines.append(re.sub(r'\[\d+:\d+(?:\.\d+)?\]', '', line).strip())
        text = '\n'.join(lines)
    if not text.strip():
        raise ValueError('No hay letra disponible.')
    record_id = record.get('id')
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 0:
        raise ValueError('Identificador de letra inválido.')
    return text, {'provider': 'LRCLIB', 'id': record_id,
                  'url': f'https://lrclib.net/api/get/{record_id}'}


def correct_words(words, lyrics):
    """Only replace isolated typos bounded by unique two-word anchors."""
    tokens = [t for t in lyrics.split() if comparison(t)]
    source = [comparison(w['text']) for w in words]
    target = [comparison(t) for t in tokens]
    matcher = SequenceMatcher(None, source, target, autojunk=False)
    operations = matcher.get_opcodes()
    result = [dict(w) for w in words]
    changes, unresolved = [], []
    # Unique anchor occurrences prevent selecting an arbitrary repeated chorus.
    def unique_pair(sequence, pair):
        return sum(sequence[i:i+2] == pair for i in range(len(sequence)-1)) == 1

    for index, (tag, a, b, c, d) in enumerate(operations):
        if tag == 'equal':
            continue
        # Prefix/suffix lyrics outside the transcribed interval are not omissions.
        if tag == 'insert' and a in (0, len(source)):
            continue
        eligible = tag == 'replace' and b-a == d-c == 1 and a >= 2 and b+2 <= len(source)
        if eligible:
            before = operations[index-1] if index else None
            after = operations[index+1] if index+1 < len(operations) else None
            eligible = (before is not None and after is not None and
                        before[0] == after[0] == 'equal' and
                        before[2]-before[1] >= 2 and after[2]-after[1] >= 2)
        if eligible:
            for pair in (source[a-2:a], source[b:b+2]):
                eligible = eligible and unique_pair(source, pair) and unique_pair(target, pair)
            eligible = eligible and bool(source[a]) and SequenceMatcher(None, source[a], target[c], autojunk=False).ratio() >= .75
        if eligible:
            word = result[a]
            changes.append({'before': word['text'], 'after': tokens[c],
                            'start': word['start_cs']/100, 'end': word['end_cs']/100})
            word['text'] = tokens[c]
        else:
            left = min(a, len(words)-1)
            right = max(left, b-1)
            unresolved.append({'start': words[left]['start_cs']/100,
                               'end': words[right]['end_cs']/100,
                               'text': ' '.join(w['text'] for w in words[a:b]),
                               'reference': ' '.join(tokens[c:d])})
    return result, changes, unresolved


def apply_lyrics(words, artist, title, issues):
    report = {'artist': artist, 'title': title, 'status': 'unavailable',
              'source': None, 'changes': [], 'unresolved': []}
    try:
        lyrics, source = fetch_lyrics(artist, title)
    except (URLError, OSError, ValueError, UnicodeError, HTTPException) as exc:
        report['reason'] = str(exc)
        notice(issues, 'lyrics_unavailable', words[0]['start_cs']/100, words[-1]['end_cs']/100,
               '', f'No se aplicaron correcciones de letra: {exc}')
        return words, report
    corrected, changes, unresolved = correct_words(words, lyrics)
    report.update(status='corrected' if changes else 'unchanged', source=source,
                  changes=changes, unresolved=unresolved)
    for change in changes:
        notice(issues, 'lyrics_corrected', change['start'], change['end'], change['after'],
               f"Letra de LRCLIB: {change['before']} → {change['after']}. Pendiente de revisión auditiva.")
    for difference in unresolved:
        notice(issues, 'lyrics_unresolved', difference['start'], difference['end'], difference['text'],
               f"Diferencia sin corregir con LRCLIB: {difference['reference']}")
    return corrected, report


def correct_transcription(path, artist, title, issues):
    """Correct the editable JSON on disk, preserving metadata and word timings."""
    from .recognition import atomic_json
    transcription = json.loads(path.read_text(encoding='utf-8'))
    words = [dict(word, start_cs=round(word['start']*100), end_cs=round(word['end']*100))
             for word in transcription['words']]
    corrected, report = apply_lyrics(words, artist, title, issues)
    for word, correction in zip(transcription['words'], corrected):
        word['text'] = correction['text']
    atomic_json(path, transcription)
    return report
