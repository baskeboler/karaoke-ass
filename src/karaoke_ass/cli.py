"""Command-line interface. No input-specific paths, names, lyrics or timestamps."""
import argparse
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sys
import time
from . import __version__

GENERATED = ('karaoke.ass','transcripcion.original.json','transcripcion.json','transcripcion.txt',
             'unidades.tsv','revision.json','revision.txt','ejecucion.json')


def parser():
    command = argparse.ArgumentParser(description='Pista vocal → transcripción → karaoke ASS. Tiempos silábicos estimados.')
    command.add_argument('audio', type=Path, help='MP3, WAV, FLAC u otro audio admitido por FFmpeg')
    command.add_argument('-o','--output', type=Path, help='Carpeta de salida (por defecto: results/NOMBRE)')
    command.add_argument('--model', default='medium', help='Modelo Whisper o ruta a checkpoint (por defecto: medium)')
    command.add_argument('--model-dir', help='Carpeta de modelos; por defecto se usa la caché de Whisper')
    command.add_argument('--language', default='auto', help='auto detecta por tramo; o código Whisper como es/en')
    command.add_argument('--device', choices=('auto','cpu','cuda'), default='auto',
                         help='auto prueba CUDA y cambia a CPU si falta memoria; cuda exige GPU')
    command.add_argument('--threads', type=int, default=4, help='Hilos de CPU (por defecto: 4)')
    command.add_argument('--chunk-seconds', type=float, default=24, help='Tramo central, 5–24 s; se añaden 2 s de contexto a cada lado')
    command.add_argument('--artist', help='Artista para buscar la letra; requiere --title')
    command.add_argument('--title', help='Título para buscar la letra; requiere --artist')
    command.add_argument('--prompt', default='', help='Contexto léxico para Whisper, por ejemplo nombres propios')
    command.add_argument('--start', type=float, default=0, help='Inicio del intervalo en segundos del audio')
    command.add_argument('--end', type=float, help='Fin del intervalo en segundos del audio')
    command.add_argument('--from-json', type=Path, help='Regenerar usando transcripcion.json o JSON Whisper con words')
    command.add_argument('--syllables', type=Path, help='Diccionario JSON de divisiones silábicas personalizadas')
    command.add_argument('--units', choices=('syllable','word'), default='syllable')
    command.add_argument('--effect', choices=('kf','k','ko'), default='kf')
    command.add_argument('--offset', type=float, default=0, help='Desplazar la salida ASS/TSV en segundos')
    command.add_argument('--max-chars', type=int, default=72, help='Caracteres aproximados por evento, en hasta dos líneas')
    command.add_argument('--max-duration', type=float, default=6, help='Duración máxima por evento salvo palabra más larga')
    command.add_argument('--font', default='DejaVu Sans')
    command.add_argument('--font-size', type=int, default=64, help='Tamaño para lienzo ASS 1920×1080')
    command.add_argument('--overwrite', action='store_true', help='Reemplazar archivos generados en la carpeta de salida')
    command.add_argument('--no-resume', action='store_true', help='Ignorar el checkpoint de reconocimiento')
    command.add_argument('--no-retry', action='store_true', help='Desactivar el segundo intento de tramos con poca cobertura')
    command.add_argument('--version', action='version', version=__version__)
    return command


def validate(options):
    if (options.artist is None) != (options.title is None):
        raise ValueError('--artist y --title deben usarse juntos.')
    if options.artist is not None:
        options.artist, options.title = options.artist.strip(), options.title.strip()
        if not options.artist or not options.title:
            raise ValueError('--artist y --title no pueden estar vacíos.')
    if not options.audio.is_file():
        raise ValueError(f'No existe el audio: {options.audio}')
    for field in ('chunk_seconds','start','offset','max_duration'):
        if not math.isfinite(getattr(options,field)):
            raise ValueError(f'{field} debe ser un número finito.')
    if options.end is not None and (not math.isfinite(options.end) or options.end<=options.start):
        raise ValueError('--end debe ser finito y mayor que --start.')
    if options.start<0 or options.threads<1 or not 5<=options.chunk_seconds<=24:
        raise ValueError('Requiere --start ≥ 0, --threads ≥ 1 y --chunk-seconds entre 5 y 24.')
    if not 20<=options.max_chars<=120 or not .5<=options.max_duration<=15 or not 12<=options.font_size<=160:
        raise ValueError('Revisa --max-chars (20–120), --max-duration (0,5–15) y --font-size (12–160).')
    if any(c in options.font for c in ',\n\r{}\\') or not options.font.strip():
        raise ValueError('Nombre de fuente no válido para ASS.')
    if options.language != 'auto' and not options.from_json:
        try:
            from whisper.tokenizer import LANGUAGES
        except ImportError as exc:
            raise ValueError('Falta Whisper. Instala: pip install ".[speech]"') from exc
        if options.language not in LANGUAGES:
            raise ValueError('Usa un código de idioma Whisper (es, en…) o auto.')
    if options.model.endswith('.en') and options.language not in ('en','auto'):
        raise ValueError('Un modelo .en solo admite inglés.')


@contextmanager
def output_lock(directory):
    """Advisory OS lock: released even if the process is killed (Linux/macOS)."""
    lock = (directory/'.run.lock').open('a+')
    try:
        try:
            import fcntl
        except ImportError as exc:
            raise ValueError('Esta versión requiere Linux o macOS para el bloqueo de ejecución.') from exc
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Otra ejecución está usando la misma carpeta de salida.') from exc
        yield
    finally:
        lock.close()


def run(options):
    from .audio import load_audio, envelope, activity, RATE
    from .recognition import recognize, file_hash, words_from_json, atomic_json
    from .timing import normalize, notice
    from .syllables import validate_overrides
    from .render import write_outputs
    started = time.monotonic()
    validate(options)
    options.overrides = validate_overrides(json.loads(options.syllables.read_text(encoding='utf-8'))) if options.syllables else {}
    options.output = options.output or Path('results')/options.audio.stem
    options.output.mkdir(parents=True, exist_ok=True)
    # Prevent --overwrite from destroying an input file placed in the output directory.
    generated = {(options.output/name).resolve() for name in GENERATED}
    for source in (options.audio,options.from_json,options.syllables):
        if source and source.resolve() in generated:
            raise ValueError('La carpeta de salida contiene un archivo usado como entrada; usa otra carpeta.')
    with output_lock(options.output):
        existing = [name for name in GENERATED if (options.output/name).exists()]
        if existing and not options.overwrite:
            raise ValueError(f'Ya existen resultados en {options.output}. Usa otra carpeta o --overwrite.')
        print('Leyendo audio…', file=sys.stderr, flush=True)
        audio = load_audio(options.audio)
        duration = len(audio)/RATE
        if options.start>=duration:
            raise ValueError('--start está fuera del audio.')
        energy = envelope(audio)
        mask = activity(energy)
        source_hash = file_hash(options.audio)
        if options.from_json:
            original = json.loads(options.from_json.read_text(encoding='utf-8-sig'))
            if not isinstance(original, dict):
                raise ValueError('La transcripción debe ser un objeto JSON.')
            if original.get('source_sha256') not in (None,source_hash):
                raise ValueError('La transcripción corresponde a otro archivo de audio (SHA-256 distinto).')
            original = dict(original, source_sha256=source_hash)
        else:
            original = recognize(audio, mask, options, options.output/'.checkpoint.json', source_hash)
        fallback = options.language if options.language != 'auto' else 'es'
        raw_words = words_from_json(original, fallback)
        issues = []
        for window in original.get('windows', []):
            if window.get('coverage', 1) < .65:
                notice(issues,'low_window_coverage',window['start'],window['end'],'',
                       'El texto cubre poca señal activa incluso después de la selección automática. Revisar posibles omisiones.')
        interval_end = min(duration, options.end if options.end is not None else duration)
        raw_words = [w for w in raw_words if options.start <= (w['start']+w['end'])/2 <= interval_end]
        # Silence rejection applies to fresh recognition only; user-corrected JSON
        # must retain intentionally placed words even when energy is very low.
        if not options.from_json:
            kept = []
            for word in raw_words:
                a,b = max(0,int(word['start']*100)), min(len(mask),math.ceil(word['end']*100))
                if b>a and not mask[a:b].any():
                    notice(issues,'silent_word',word['start'],word['end'],word['text'],'Texto producido en silencio eliminado del ASS; se conserva en el JSON original.')
                else:
                    kept.append(word)
            raw_words = kept
        words = normalize(raw_words, duration, issues)
        if not words:
            # Keep diagnostics so a silent input can be distinguished from model failure.
            atomic_json(options.output/'transcripcion.original.json',original)
            raise ValueError('No se reconocieron palabras con tiempos utilizables; no se generó un ASS vacío.')
        if options.start or options.end is not None:
            original['interval'] = [options.start,interval_end]
        report = write_outputs(options.output, words, original, energy, mask, issues, options, duration)
        lyrics_report = report.get('lyrics')
        manifest = {'version':__version__, 'source':str(options.audio.resolve()), 'source_sha256':source_hash,
                    'duration':duration, 'elapsed_seconds':round(time.monotonic()-started,2),
                    'model':original.get('model'), 'from_json':str(options.from_json) if options.from_json else None,
                    'recognition_device':original.get('device'), 'cuda_oom_fallback':original.get('cuda_oom_fallback',False),
                    'options':{k:str(v) if isinstance(v,Path) else v for k,v in vars(options).items() if k!='overrides'},
                    'lines':report['lines'],'units':report['units'],'review_issues':len(issues),
                    'status':'draft_requires_review'}
        if lyrics_report is not None:
            manifest['lyrics'] = lyrics_report
        atomic_json(options.output/'ejecucion.json',manifest)
    print(f'ASS generado: {(options.output/"karaoke.ass").resolve()}')
    print(f'{report["lines"]} líneas, {report["units"]} unidades, {len(issues)} avisos. Cortes silábicos estimados.')
    return 0


def main(argv=None):
    options = parser().parse_args(argv)
    try:
        return run(options)
    except KeyboardInterrupt:
        print('\nInterrumpido. Repite el comando para reanudar los tramos guardados.',file=sys.stderr)
        return 130
    except (ValueError,OSError,RuntimeError) as exc:
        print(f'Error: {exc}',file=sys.stderr)
        return 2
