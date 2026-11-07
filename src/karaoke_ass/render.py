"""ASS output with exact centisecond sums, plus machine-editable companions."""
import csv
import json
import sys
from pathlib import Path
import re
from .recognition import atomic_json


def timestamp(cs: int) -> str:
    hours, rest = divmod(cs, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, fraction = divmod(rest, 100)
    return f'{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}'


def safe_text(text: str) -> str:
    # Prevent recognized lyrics from becoming ASS override commands.
    return ' '.join(text.replace('\\', '＼').replace('{', '｛').replace('}', '｝').split())


def separator(previous: str, current: str) -> str:
    return '' if not previous or current.startswith(('.', ',', ';', ':', '!', '?', '-', "'", '’', ')', ']')) else ' '


def plain_text(words) -> str:
    text = ''
    for word in words:
        text += separator(text, word['text']) + word['text']
    return text


def group_lines(words, max_chars=72, max_duration=6.0):
    groups, current = [], []
    for word in words:
        if current and (word['end_cs']-current[0]['start_cs'] > max_duration*100
                        or len(plain_text(current+[word])) > max_chars
                        or word['start_cs']-current[-1]['end_cs'] > 100):
            groups.append(current)
            current = []
        current.append(word)
        if len(plain_text(current)) >= 18 and word['text'].endswith(('.', '!', '?', ',')):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def shifted_words(words, offset: float):
    delta = round(offset*100)
    result = []
    for word in words:
        units = [dict(u, source_start_cs=u['start_cs'], source_end_cs=u['end_cs'],
                      start_cs=max(0,u['start_cs']+delta), end_cs=u['end_cs']+delta)
                 for u in word['units'] if u['end_cs']+delta > 0]
        if units:
            result.append(dict(word, units=units, start_cs=units[0]['start_cs'], end_cs=units[-1]['end_cs']))
    return result


def render(words, options):
    shifted = shifted_words(words, options.offset)
    header = f'''[Script Info]
Title: Karaoke automático - revisar informe
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes
; Tiempos silábicos estimados. Ver revision.json y revision.txt.
; Desplazamiento de salida: {options.offset:+.2f} segundos.
; Vista previa del texto: 200 ms antes del canto, limitada al inicio del audio.

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{options.font},{options.font_size},&H0000FFFF,&H00FFFFFF,&H00181818,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,110,110,85,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events, rows, transcript = [], [], []
    for line_number, group in enumerate(group_lines(shifted, options.max_chars, options.max_duration), 1):
        singing_start, end = group[0]['start_cs'], group[-1]['end_cs']
        start = max(0, singing_start-20)
        line_text = plain_text(group)
        break_at = -1
        if len(line_text) > 42 and len(group) > 1:
            break_at = min(range(1,len(group)), key=lambda k:abs(len(plain_text(group[:k]))-len(plain_text(group[k:]))))
        cursor, text, previous = start, '', ''
        for wi, word in enumerate(group):
            if word['start_cs'] > cursor:
                text += r'{\k' + str(word['start_cs']-cursor) + '}'
            for ui, unit in enumerate(word['units']):
                duration = unit['end_cs']-unit['start_cs']
                prefix = (r'\N' if wi == break_at else separator(previous, word['text'])) if ui == 0 else ''
                text += '{' + chr(92) + options.effect + str(duration) + '}' + prefix + safe_text(unit['text'])
                rows.append({'linea': line_number, 'inicio_s': unit['start_cs']/100,
                             'fin_s': unit['end_cs']/100, 'inicio_audio_s': unit['source_start_cs']/100,
                             'fin_audio_s': unit['source_end_cs']/100, 'palabra': word['text'],
                             'unidad': ui+1, 'texto': unit['text'], 'idioma': word['language'],
                             'metodo': 'limite_palabra' if options.units=='word' else 'silaba_estimada_energia'})
                cursor = unit['end_cs']
            previous = word['text']
        durations = sum(int(x) for x in re.findall(r'\\k(?:f|o)?(\d+)', text))
        if durations != end-start:
            raise ValueError('Error interno: las unidades no suman la duración del evento.')
        events.append(f'Dialogue: 0,{timestamp(start)},{timestamp(end)},Default,,0,0,0,,{text}')
        transcript.append(f'[{timestamp(singing_start)} --> {timestamp(end)}] {line_text}')
    return header+'\n'.join(events)+'\n', rows, transcript


def write_outputs(directory: Path, words, original, energy, mask, issues, options, duration, lyrics_report=None):
    from .timing import timed_units, coverage_issues
    normalized = {'schema_version': 1, 'timeline': 'source_audio_seconds',
                  'duration': duration, 'source_sha256': original.get('source_sha256'),
                  'interval': original.get('interval', [0, duration]),
                  'words': [{k:v for k,v in word.items() if k not in ('start_cs','end_cs')}
                            | {'start':word['start_cs']/100, 'end':word['end_cs']/100} for word in words]}
    # Persist raw and normalized text separately so corrections can be regenerated.
    atomic_json(directory/'transcripcion.original.json', original)
    atomic_json(directory/'transcripcion.json', normalized)
    if getattr(options, 'artist', None) is not None:
        from .lyrics import correct_transcription
        print('Corrigiendo transcripcion.json con la letra de LRCLIB…', file=sys.stderr, flush=True)
        lyrics_report = correct_transcription(directory/'transcripcion.json', options.artist, options.title, issues)
        if lyrics_report['status'] == 'unavailable':
            print('Aviso: ' + lyrics_report['reason'] + ' Se conserva la transcripción.', file=sys.stderr)
    # The corrected JSON is the canonical input for syllables and ASS rendering.
    saved = json.loads((directory/'transcripcion.json').read_text(encoding='utf-8'))
    words = [dict(word, start_cs=round(word['start']*100), end_cs=round(word['end']*100))
             for word in saved['words']]
    units = timed_units(words, energy, options.overrides, options.units, issues)
    interval = saved['interval']
    coverage_issues(words, mask, issues, interval[0], interval[1])
    ass, rows, transcript = render(units, options)
    if not rows:
        raise ValueError('No quedan subtítulos visibles después del desplazamiento.')
    (directory/'karaoke.ass').write_text(ass, encoding='utf-8-sig')
    (directory/'transcripcion.txt').write_text('\n'.join(transcript)+'\n', encoding='utf-8')
    with (directory/'unidades.tsv').open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)
    report = {'syllable_timing': 'estimated', 'verified_by_listening': False,
              'offset_seconds': options.offset, 'preview_seconds': 0.2, 'issue_timeline': 'source_audio_seconds',
              'lines':len(transcript), 'units':len(rows), 'issues':issues}
    if lyrics_report is not None:
        report['lyrics'] = lyrics_report
    atomic_json(directory/'revision.json', report)
    summary = ['BORRADOR AUTOMÁTICO DE KARAOKE', '',
               'Los cortes entre sílabas son estimaciones, no una alineación fonética verificada.',
               'La división ortográfica usa reglas aproximadas para español e inglés.',
               'Puede haber errores de texto, voces omitidas, sinalefas y melismas sin representar.',
               f'Desplazamiento del ASS: {options.offset:+.2f} s. Los avisos usan tiempos del audio original.',
               f'{len(transcript)} líneas; {len(rows)} unidades; {len(issues)} avisos.', '']
    if lyrics_report is not None:
        summary.append(f"Letra LRCLIB: {lyrics_report['status']}; {len(lyrics_report['changes'])} correcciones.")
        if lyrics_report['source']:
            summary.append('Fuente: ' + lyrics_report['source']['url'])
    for issue in issues:
        summary.append(f"[{issue['start']:.2f}–{issue['end']:.2f}] {issue['code']}: {issue['text']} — {issue['message']}")
    (directory/'revision.txt').write_text('\n'.join(summary)+'\n', encoding='utf-8')
    return report
