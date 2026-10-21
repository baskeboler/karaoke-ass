"""Time normalization, explicit uncertainty, and estimated within-word boundaries."""
import re
import numpy as np
from .syllables import split, key


def notice(issues, code, start, end, text, message):
    issues.append({'code': code, 'start': round(max(0, start), 3), 'end': round(max(0, end), 3),
                   'text': text, 'message': message})


def normalize(words: list[dict], duration: float, issues: list) -> list[dict]:
    usable = []
    for word in words:
        if word.get('missing_segment'):
            notice(issues, 'segment_without_words', word['start'], word['end'], '', 'El reconocedor produjo un segmento sin tiempos de palabra.')
            continue
        if not word['text'].strip():
            continue
        start = max(0, min(round(duration*100), round(word['start']*100)))
        end = max(0, min(round(duration*100), round(word['end']*100)))
        if word['end'] < word['start']:
            raise ValueError(f'Tiempos invertidos para {word["text"]!r}.')
        if word['start'] < 0 or word['end'] > duration:
            notice(issues, 'clipped', word['start'], word['end'], word['text'], 'Palabra recortada a la duración del audio.')
        usable.append(dict(word, start_cs=start, end_cs=end))
    if any(a['start_cs'] > b['start_cs'] for a,b in zip(usable, usable[1:])):
        notice(issues, 'reordered', 0, duration, '', 'Se ordenaron palabras cuyos tiempos no eran crecientes.')
    usable.sort(key=lambda w: (w['start_cs'], w['end_cs']))
    out = []
    for word in usable:
        a, b = word['start_cs'], word['end_cs']
        if out:
            prev = out[-1]
            overlap = min(b, prev['end_cs'])-max(a, prev['start_cs'])
            same = key(re.sub(r'\W', '', word['text'])) == key(re.sub(r'\W', '', prev['text']))
            if same and overlap > 0 and overlap >= .5*max(1, min(b-a, prev['end_cs']-prev['start_cs'])):
                notice(issues, 'duplicate', a/100, b/100, word['text'], 'Duplicado con tiempos superpuestos eliminado.')
                continue
            if b <= a and b-prev['end_cs'] > 20:
                notice(issues, 'unplaceable', a/100, b/100, word['text'], 'Palabra de duración cero tras un silencio; se conserva en el JSON original.')
                continue
            if a < prev['end_cs'] or b <= a:
                # Share only the previous word's interval where needed. Never
                # extend the entire song to make a zero-duration token fit.
                boundary = min(a, b)
                if b <= prev['end_cs'] or b <= a:
                    boundary = min(b-1, max(prev['start_cs']+1, b-min(25, max(1, (b-prev['start_cs'])//3))))
                boundary = max(prev['start_cs']+1, boundary)
                if boundary >= b:
                    notice(issues, 'unplaceable', a/100, b/100, word['text'], 'Palabra sin espacio temporal; consultar transcripcion.original.json.')
                    continue
                prev['end_cs'] = boundary
                a = max(a, boundary) if a < b else boundary
                notice(issues, 'timing_repaired', a/100, b/100, word['text'], 'Límite de palabra estimado al reparar solapamiento o duración cero.')
        if b <= a:
            # First zero-width token: leave it in raw JSON, not as a fake event.
            notice(issues, 'unplaceable', a/100, b/100, word['text'], 'Palabra sin duración; consultar transcripcion.original.json.')
            continue
        word = dict(word, start_cs=a, end_cs=b)
        if word.get('probability') is not None and word['probability'] < .5:
            notice(issues, 'low_confidence', a/100, b/100, word['text'], 'Confianza de palabra inferior a 0,5; es orientativa, no una garantía.')
        if b-a > 200:
            notice(issues, 'long_word', a/100, b/100, word['text'], 'Palabra de más de 2 s: puede ser vocal sostenida, texto omitido o un tiempo incorrecto.')
        out.append(word)
    return out


def boundaries(start: int, end: int, count: int, energy: np.ndarray) -> list[int]:
    if end-start < count:
        raise ValueError('No hay suficientes centésimas para las unidades solicitadas.')
    cuts = [start]
    for index in range(1, count):
        target = start+(end-start)*index/count
        radius = max(1.0, (end-start)/count*.25)
        lo = max(cuts[-1]+1, int(target-radius))
        hi = min(end-(count-index), int(target+radius))
        grid = np.arange(lo, hi+1)
        if len(grid):
            values = energy[np.clip(grid, 0, len(energy)-1)]
            score = values/(float(values.max())+1e-12)+.35*((grid-target)/radius)**2
            cut = int(grid[np.argmin(score)])
        else:
            cut = max(cuts[-1]+1, min(end-(count-index), round(target)))
        cuts.append(cut)
    return cuts+[end]


def timed_units(words, energy, overrides, unit_mode, issues):
    result = []
    for word in words:
        language = word.get('language', 'es')
        parts = [word['text']] if unit_mode == 'word' else split(word['text'], language, overrides)
        if language not in ('es', 'en') and unit_mode == 'syllable':
            notice(issues, 'unsupported_syllables', word['start_cs']/100, word['end_cs']/100, word['text'], f'Idioma {language}: se conserva la palabra entera.')
        if len(parts) > word['end_cs']-word['start_cs']:
            notice(issues, 'too_short', word['start_cs']/100, word['end_cs']/100, word['text'], 'Palabra demasiado breve para separar sílabas; se conserva entera.')
            parts = [word['text']]
        cuts = boundaries(word['start_cs'], word['end_cs'], len(parts), energy)
        result.append(dict(word, units=[{'text':part, 'start_cs':a, 'end_cs':b}
                                       for part,a,b in zip(parts,cuts,cuts[1:])]))
    return result


def coverage_issues(words, mask, issues, start=0, end=None):
    covered = np.zeros(len(mask), dtype=bool)
    for word in words:
        covered[max(0, word['start_cs']-15):min(len(mask), word['end_cs']+15)] = True
    missing = mask & ~covered
    missing[:max(0, round(start*100))] = False
    if end is not None:
        missing[round(end*100):] = False
    # Bridge tiny energy dips before counting an uncovered region.
    active = np.flatnonzero(missing)
    if not len(active):
        return
    runs = np.split(active, np.flatnonzero(np.diff(active)>25)+1)
    for run in runs:
        if len(run) >= 80 and run[-1]-run[0] >= 100:
            notice(issues, 'uncovered_activity', int(run[0])/100, int(run[-1]+1)/100, '',
                   'Señal con energía sin texto reconocido. Puede ser voz omitida o residuo instrumental.')
