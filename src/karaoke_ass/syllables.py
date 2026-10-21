"""Approximate orthographic syllables; never presented as phonetic alignment."""
import re
import unicodedata

VOWELS = set('aeiouáéíóúü')
OPEN = set('aeoáéó')
CLUSTERS = {'bl', 'br', 'cl', 'cr', 'dr', 'fl', 'fr', 'gl', 'gr', 'pl', 'pr', 'tr', 'ch', 'll', 'rr', 'qu', 'gu'}


def key(text: str) -> str:
    return unicodedata.normalize('NFC', text).casefold()


def validate_overrides(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('El diccionario silábico debe ser un objeto JSON.')
    result = {}
    for word, parts in data.items():
        if (not isinstance(word, str) or not word or not isinstance(parts, list)
                or not parts or not all(isinstance(p, str) and p for p in parts)
                or key(''.join(parts)) != key(word)):
            raise ValueError(f'División no válida para {word!r}: las partes deben reconstruir la palabra.')
        result[key(word)] = parts
    return result


def _spanish(word: str) -> list[int]:
    lower = word.casefold()
    positions = []
    for i, char in enumerate(lower):
        if char not in VOWELS:
            continue
        if char == 'u' and i > 0 and lower[i-1] in 'qg' and i+1 < len(word) and lower[i+1] in 'eiéí':
            continue
        positions.append(i)
    nuclei = []
    for i in positions:
        if nuclei:
            prev = nuclei[-1][-1]
            pair = lower[prev] + lower[i]
            # h between vowels is transparent to a diphthong/hiatus.
            adjacent = i == prev + 1 or lower[prev+1:i] == 'h'
            hiatus = (lower[prev] in OPEN and lower[i] in OPEN) or any(c in 'íú' for c in pair) or pair[0] == pair[1]
            # A three-vowel nucleus is only a weak-strong-weak triphthong.
            can_join = len(nuclei[-1]) == 1 or (len(nuclei[-1]) == 2 and lower[nuclei[-1][0]] in 'iuü' and lower[prev] in OPEN and lower[i] in 'iuü')
            if adjacent and not hiatus and can_join:
                nuclei[-1].append(i)
                continue
        nuclei.append([i])
    cuts = [0]
    for left, right in zip(nuclei, nuclei[1:]):
        a, b = left[-1], right[0]
        cluster = lower[a+1:b]
        if not cluster:
            cut = b
        elif len(cluster) == 1:
            cut = a + 1
        else:
            cut = b - (2 if cluster[-2:] in CLUSTERS else 1)
        cuts.append(cut)
    return cuts + [len(word)]


def _english(word: str) -> list[int]:
    """Vowel-group heuristic, including silent terminal e; exceptions use JSON."""
    lower = word.casefold()
    if "'" in lower or '’' in lower:
        return [0, len(word)]
    nuclei = list(re.finditer(r'[aeiouy]+', lower))
    if len(nuclei) > 1 and lower.endswith('e') and not re.search(r'[^aeiou]le$', lower):
        if nuclei[-1].start() == len(lower)-1:
            nuclei.pop()
    if len(nuclei) > 1 and lower.endswith('ed') and not lower.endswith(('ted', 'ded')):
        if nuclei[-1].group() == 'e' and nuclei[-1].start() == len(lower)-2:
            nuclei.pop()
    cuts = [0]
    for left, right in zip(nuclei, nuclei[1:]):
        cuts.append(max(left.end(), right.start()-1))
    return sorted(set(cuts + [len(word)]))


def split(text: str, language: str, overrides: dict | None = None) -> list[str]:
    match = re.fullmatch(r'([^\w]*)(.*?)([^\w]*)', text, re.DOTALL)
    prefix, word, suffix = match.groups()
    if not word:
        return [text]
    override = (overrides or {}).get(key(word))
    if override:
        cuts = [0]
        for part in override:
            cuts.append(cuts[-1]+len(part))
    elif language == 'es':
        cuts = _spanish(word)
    elif language == 'en':
        cuts = _english(word)
    else:
        cuts = [0, len(word)]
    parts = [word[a:b] for a, b in zip(cuts, cuts[1:]) if b > a] or [word]
    parts[0] = prefix + parts[0]
    parts[-1] += suffix
    assert ''.join(parts) == text
    return parts
