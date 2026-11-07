# Vocals → karaoke ASS

Herramienta local para transformar una pista vocal de canción en subtítulos ASS de karaoke. Recibe un archivo y produce `karaoke.ass`, texto con timestamps, tiempos por sílaba y un informe de revisión. No requiere una API de pago ni envía el audio a un servicio remoto. Whisper puede descargar el modelo elegido en el primer uso.

**El resultado es un borrador automático.** Los tiempos de palabra proceden de Whisper. La división ortográfica y los cortes internos entre sílabas se estiman con reglas de español/inglés y la energía del audio. No equivalen a una alineación fonética ni garantizan una letra correcta. Sinalefas, melismas, coros simultáneos y pronunciaciones especiales requieren revisión.

## Instalación

Requiere Linux o macOS, Python 3.10 o posterior y FFmpeg accesible en el sistema. La versión de Whisper usada y probada es `20250625`; también se probó con PyTorch 2.10.0 y NumPy 2.4.3.

Desde esta carpeta:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[speech]'
```

PyTorch puede instalar paquetes grandes. En un equipo que solo usará CPU se puede instalar primero la variante de CPU, siguiendo las instrucciones de [PyTorch](https://pytorch.org/get-started/locally/), y después instalar este proyecto.

Si ya tienes un entorno con Whisper y NumPy, puedes reutilizarlo sin instalar otra copia:

```bash
KARAOKE_PYTHON=/ruta/al/entorno/bin/python ./karaoke-ass vocals.mp3
```

El lanzador elige, en este orden: `KARAOKE_PYTHON`, `.venv/bin/python` o `python3`. También se puede instalar el paquete y usar el comando `karaoke-ass` desde cualquier carpeta.

## Entorno de desarrollo con mise

Instala [mise](https://mise.jdx.dev/getting-started.html) y FFmpeg con el gestor de paquetes de tu sistema. Desde la raíz del proyecto:

```bash
mise trust
mise install
mise run setup
mise run test
```

`mise.toml` selecciona Python 3.12 y crea/activa `.venv` automáticamente. `setup` instala el paquete en modo editable con sus dependencias básicas; no descarga Whisper ni sus modelos. La configuración reutiliza una `.venv` existente: si proviene de otra versión de Python, renómbrala antes de inicializar para que mise cree una nueva con Python 3.12. `.venv` permanece ignorada por Git.

| Tarea | Función |
| --- | --- |
| `mise run setup` | Instalar o actualizar el entorno básico de desarrollo y tests. |
| `mise run setup:speech` | Instalar además Whisper/PyTorch para transcribir audio. |
| `mise run check:ffmpeg` | Comprobar FFmpeg, requerido por la suite completa y el procesamiento de audio. |
| `mise run test` | Ejecutar toda la suite, comprobando antes FFmpeg. |
| `mise run test:lyrics` | Ejecutar solo los tests de letras, sin necesitar FFmpeg. |

Para trabajar con reconocimiento real, ejecuta `mise run setup:speech` y luego `mise exec -- karaoke-ass vocals.wav`. Esta instalación puede descargar dependencias grandes; los modelos se descargan al transcribir por primera vez. Las tareas de tests no instalan dependencias: ejecuta `setup` primero. El reconocimiento Whisper y las consultas HTTP se simulan en los tests. Puedes usar `mise exec -- python …` sin activar mise en tu shell.

## Uso

```bash
./karaoke-ass /ruta/vocals.mp3 --output ./results/mi-cancion
```

Ese único comando realiza todo el proceso. Por defecto usa Whisper `medium`, elige CPU o CUDA disponible y detecta el idioma por tramo. Mantiene los timestamps del archivo original, incluso si la canción empieza con silencio.

Con `--device auto` (predeterminado), si CUDA se queda sin memoria al cargar el modelo, transcribir o reintentar un tramo, el proceso libera sus referencias al modelo GPU y vuelve a intentar ese trabajo en CPU. Conserva el mismo modelo y los tramos completados; los siguientes se procesan en CPU. La salida y el archivo `ejecucion.json` registran el cambio. `--device cuda` exige GPU: ante falta de memoria se detiene con un mensaje explicativo.

Si aparece `CUDA out of memory`, puedes continuar directamente en CPU:

```bash
KARAOKE_PYTHON=.venv/bin/python ./karaoke-ass vocals.mp3 --device cpu
```

El modelo `medium` tiene un requisito orientativo de unos 5 GB de VRAM según [Whisper](https://github.com/openai/whisper#available-models-and-languages). En una GPU de aproximadamente 4 GB, usa CPU para conservar `medium`, o prueba `--model small --device cuda` (unos 2 GB orientativos). El consumo real depende del audio y de otros procesos. Cambiar solo el dispositivo permite reutilizar los tramos guardados; cambiar el modelo invalida esa caché. La CPU puede tardar más.

Para una pista en español con nombres propios conocidos:

```bash
./karaoke-ass vocals.mp3 --language es --prompt 'Nombres de artistas, lugares o marcas.' --output results/cancion
```

Para canciones que cambian entre español e inglés, conserva `--language auto`. La detección ocurre por tramo, por lo que puede fallar cuando el idioma cambia dentro del mismo tramo. `--chunk-seconds 12` permite tramos más cortos, con menos contexto lingüístico. Forzar un idioma se aplica a toda la ejecución y no traduce la letra.

```bash
./karaoke-ass vocals.mp3 --model small --device cpu --threads 4 --output results/prueba
./karaoke-ass vocals.mp3 --start 12 --end 35 --output results/fragmento
```

`small` es una opción para probar con menos recursos. La calidad depende de la pista; el rendimiento no se garantiza. Un fragmento conserva sus tiempos originales, no vuelve a comenzar en cero. Los modelos se obtienen y almacenan mediante la caché habitual de Whisper; `--model-dir` permite elegir otra carpeta.

## Archivos de salida

| Archivo | Contenido |
| --- | --- |
| `karaoke.ass` | Karaoke con resaltado progresivo `\kf`, dos líneas cuando hace falta, fuente blanca y resaltado amarillo. |
| `transcripcion.txt` | Texto por evento con timestamps de salida. |
| `transcripcion.json` | Palabras y tiempos normalizados, editables, en la línea temporal del audio original. |
| `transcripcion.original.json` | Reconocimiento original, antes de descartar duplicados o reparar tiempos. |
| `unidades.tsv` | Inicio/fin de cada unidad en la salida y en el audio original. |
| `revision.txt`, `revision.json` | Avisos de confianza baja, palabras demasiado largas, reparaciones, posibles omisiones e idiomas sin división silábica. |
| `ejecucion.json` | Opciones utilizadas, hash SHA-256 del audio, duración y estadísticas. |
| `.checkpoint.json` | Resultados de reconocimiento por tramo, para reanudar. |

El informe siempre indica que falta revisión de oído, aunque no aparezcan avisos específicos. La confianza de Whisper es orientativa. La detección de energía no distingue necesariamente una voz de restos instrumentales.

## Corregir y regenerar
ruta/vocals.mp3 --output resultados/cancion
Edita **una copia** de `transcripcion.json`: puedes cambiar `text`, `start`, `end` y `language` de cada palabra. Sus tiempos están en segundos del audio original. Cada palabra puede tener `language: "es"` o `"en"`; otros idiomas se conservan por palabra y se señalan en el informe.

```bash
./karaoke-ass vocals.mp3 --from-json letra-corregida.json --output results/corregido
```

Esto vuelve a calcular el ASS y las unidades sin cargar Whisper. Se comprueba el hash del audio si el JSON lo incluye. También se admite un JSON directo de Whisper con `segments[].words[]`. No se admite un archivo de letra sin tiempos: requiere alineación adicional.

Para nombres o préstamos, crea un diccionario de divisiones que reconstruyan exactamente la palabra:

```json
{
  "Spotify": ["Spo", "ti", "fy"],
  "podcast": ["pod", "cast"]
}
```

```bash
./karaoke-ass vocals.mp3 --from-json letra-corregida.json --syllables silabas.json --output results/ajustado
```

El diccionario controla la división textual, no corrige la letra ni determina límites fonéticos exactos. Las reglas de inglés son especialmente aproximadas.

Opciones de presentación:

```bash
./karaoke-ass vocals.mp3 --units word --effect k --offset 0.25 --output results/palabras
./karaoke-ass vocals.mp3 --font 'DejaVu Sans' --font-size 64 --max-chars 72 --max-duration 6 --output results/estilo
```

`--offset` desplaza el ASS, el texto con timestamps y las columnas de salida del TSV. Los tiempos del JSON editable y los avisos conservan la referencia al audio original. Un desplazamiento negativo recorta las unidades que quedarían antes de cero. `--effect` acepta `kf`, `k` y `ko`. Las duraciones ASS se cuantizan a centésimas y se comprueba que sumen exactamente la duración de cada evento.

## Interrupciones y repetición

Si se interrumpe el reconocimiento, repite el mismo comando. Reutiliza los tramos del checkpoint cuando coinciden el contenido del audio, el modelo y las opciones de reconocimiento. Un cambio de audio, idioma, prompt o tamaño de tramo invalida esa caché. El recálculo del estilo y las sílabas no invalida el reconocimiento.

Si ya existen archivos finales, el programa se detiene para evitar una sobrescritura accidental. Usa otra carpeta o `--overwrite`. `--no-resume` obliga a repetir el reconocimiento. El bloqueo de carpeta evita dos ejecuciones simultáneas sobre el mismo destino.

Las entradas sin palabras utilizables producen un error y conservan el reconocimiento original para diagnóstico; no se genera un ASS vacío. Ante un error, revisa el mensaje y el checkpoint. Códigos de salida: 0, resultado generado; 2, error; 130, interrupción. El código 0 confirma la generación de un borrador válido, no la exactitud de la letra.

## Procesar una carpeta

```bash
for pista in /ruta/vocals/*.mp3; do
  [ -f "$pista" ] || continue
  nombre="$(basename "${pista%.mp3}")"
  ./karaoke-ass "$pista" --output "results/$nombre" || exit "$?"
done
```

Se procesa una canción por ejecución. Usa carpetas de salida diferentes para archivos con el mismo nombre base.

## Cómo funciona

1. FFmpeg convierte a mono de 16 kHz para el reconocimiento.
2. Se calcula una envolvente de energía de 10 ms y se recortan bordes silenciosos de cada ventana.
3. Whisper reconoce tramos centrales de 24 s con hasta 2 s de contexto a cada lado, sin arrastrar la letra de la ventana anterior. Se desactiva el descarte global por probabilidad de no voz, que había omitido partes de la canción de referencia; los silencios se comprueban con energía.
4. Cada palabra se asigna por su punto medio a un solo tramo central. Se eliminan repeticiones superpuestas, conservando las repeticiones cantadas en tiempos distintos.
5. Se normalizan tiempos y se documentan reparaciones. Las palabras imposibles de ubicar se conservan en el reconocimiento original y se señalan.
6. Si el reconocimiento cubre menos del 65 % de la señal activa de un tramo, se hace un segundo intento acotado con subtramos de hasta 12 s, sin prompt. Se selecciona por cobertura y confianza orientativa; esto no verifica la letra. `--no-retry` desactiva este paso.
7. Se divide el texto en unidades y se estiman los cortes internos cerca de mínimos de energía dentro del intervalo de cada palabra.
8. Se agrupan frases y se genera ASS, conservando las pausas con etiquetas de duración vacías. Se generan los archivos de edición y revisión.

Este programa automatiza la primera versión que antes armamos con varias ejecuciones y selección manual de fragmentos. No incluye reglas específicas de aquella canción.

## Pruebas

Con el entorno inicializado, ejecuta `mise run test` o `mise run test:lyrics`. También puedes ejecutar directamente:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Las pruebas cubren silabeo, conservación del texto, tiempos inválidos, límites de ventana, repeticiones, reanudación, protección de salidas, pausas, desplazamientos y regeneración. Las pruebas del reconocedor usan un modelo simulado para comprobar el ensamblado; la prueba de reconocimiento con audio real se documenta por separado en `VALIDACION.md`.

Referencias: [Whisper](https://github.com/openai/whisper), [etiquetas ASS de karaoke de Aegisub](https://aegisub.org/docs/latest/ass_tags/#karaoke-effect).

## Corrección opcional con letras de internet

```bash
./karaoke-ass vocals.wav --artist "Modern Talking" --title "Cheri Cheri Lady"
```

`--artist` y `--title` deben incluirse juntos y no pueden estar vacíos. Después de completar Whisper, se consulta [LRCLIB](https://lrclib.net/docs) por artista y título, sin clave de API. También funciona con `--from-json`, para corregir una transcripción existente sin repetir Whisper. Sin estos argumentos no se consulta internet. Solo se envían artista y título; el audio y la transcripción permanecen locales.

Las correcciones son conservadoras: se sustituyen únicamente palabras de similitud mínima 0,75 entre anclas únicas de al menos dos palabras coincidentes a cada lado. No se agregan ni eliminan palabras, ni se cambian sus tiempos. Las repeticiones ambiguas y otras diferencias se señalan para revisión. Las letras anteriores o posteriores al intervalo transcrito no se agregan. Los tiempos LRC no se utilizan.

Primero se guarda `transcripcion.json`, luego se corrige ese archivo con la letra descargada y se vuelve a cargar el JSON corregido para generar ASS, TXT y TSV. `transcripcion.original.json` conserva la entrada sin corregir; el checkpoint tampoco cambia. `revision.json` y `ejecucion.json` registran la fuente, el identificador, el estado, las sustituciones y las diferencias sin resolver; `revision.txt` incluye un resumen y los avisos. El resultado sigue siendo un borrador pendiente de revisión auditiva.

Si la consulta falla, tarda más de 10 segundos, no devuelve letra o sus metadatos no coinciden, se muestra un aviso y se generan los resultados con el texto original. El catálogo puede no incluir la canción o su versión específica.


### Anticipación visual del karaoke

Cada evento del ASS muestra el texto 200 ms antes de la primera palabra, sin adelantar el resaltado ni modificar los tiempos del JSON, TXT o TSV. Al comienzo del archivo, la anticipación se limita a cero. Los eventos consecutivos pueden coincidir durante esos 200 ms para mantener el final cantado de la frase anterior. Esta anticipación es independiente de `--offset`, que desplaza la sincronización completa.
