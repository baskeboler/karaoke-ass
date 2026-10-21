"""Decode once with FFmpeg and use a lightweight activity/energy envelope."""
from pathlib import Path
import subprocess
import numpy as np

RATE = 16000
HOP = 160  # one centisecond


def load_audio(path: Path) -> np.ndarray:
    try:
        process = subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path.resolve()),
             '-vn', '-ar', str(RATE), '-ac', '1', '-f', 'f32le', 'pipe:1'],
            capture_output=True, check=True,
        )
    except FileNotFoundError as exc:
        raise ValueError('Falta FFmpeg. Instálalo antes de procesar audio.') from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f'FFmpeg no pudo leer el audio: {exc.stderr.decode(errors="replace")[-800:]}') from exc
    audio = np.frombuffer(process.stdout, dtype='<f4').copy()
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError('El audio está vacío o contiene muestras no válidas.')
    return audio


def envelope(audio: np.ndarray) -> np.ndarray:
    padded = np.pad(audio, (0, (-len(audio)) % HOP))
    rms = np.sqrt(np.mean(padded.reshape(-1, HOP).astype(np.float64) ** 2, axis=1))
    # Preserve length even with clips shorter than the smoothing kernel.
    return np.convolve(np.pad(rms, (2, 2), mode='edge'), np.ones(5) / 5, mode='valid')


def activity(energy: np.ndarray) -> np.ndarray:
    # Activity is signal energy, not a phonetic or voice detector.
    threshold = max(0.0003, float(np.percentile(energy, 95)) * 0.06)
    return energy > threshold


def active_slice(mask: np.ndarray, start: int, end: int) -> tuple[int, int] | None:
    """Trim quiet edges; returned sample indexes remain on the source timeline."""
    lo, hi = start // HOP, min(len(mask), (end + HOP - 1) // HOP)
    found = np.flatnonzero(mask[lo:hi])
    if not len(found):
        return None
    return max(start, (lo + int(found[0]) - 15) * HOP), min(end, (lo + int(found[-1]) + 16) * HOP)
