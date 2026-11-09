# Uso en este equipo

La aplicación se ejecuta ahora en Clojure/JVM: requiere Java 17+, Clojure CLI y FFmpeg. `./karaoke-ass --from-json …` no necesita Python. Para reconocimiento nuevo, `KARAOKE_PYTHON` selecciona únicamente el proceso de inferencia Whisper. Usa `clojure -M:test` para las pruebas de la implementación actual.

Hay un entorno con Whisper instalado en `/home/victorgil/tmp/venv` y modelos `base`, `small` y `medium` ya descargados. Esta es una configuración local, no un requisito del proyecto.

Desde la carpeta de la herramienta:

```bash
export KARAOKE_PYTHON=/home/victorgil/tmp/venv/bin/python
./karaoke-ass /ruta/a/otra-pista-vocal.mp3 --output /ruta/a/resultados/cancion
```

Para la pista de esta conversación:

```bash
export KARAOKE_PYTHON=/home/victorgil/tmp/venv/bin/python
./karaoke-ass /home/victorgil/tmp/demucs/separated/htdemucs/elonmusk/vocals.mp3 \
  --output ../mi-nueva-transcripcion \
  --model medium --device cpu --threads 4 \
  --prompt 'Elon Musk. Donald Trump. Spotify.'
```

Para canciones con idiomas mezclados se recomienda comenzar con `--language auto`, que es el valor predeterminado. No pongas nombres ajenos a la pista en el prompt: pueden sesgar el reconocimiento.

El ejemplo generado durante la validación está en la carpeta vecina `karaoke-ass-ejemplo`. La fuente de audio y el entorno Python no se copian al proyecto.
