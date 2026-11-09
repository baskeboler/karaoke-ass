# Reorganización de fuentes tras la migración

La aplicación Clojure está en `src/karaoke_ass/` y sus pruebas en `test/karaoke_ass/`. El adaptador Whisper y sus pruebas Python están en `legacy/`. Se eliminaron la aplicación Python sustituida, sus pruebas, `pyproject.toml` y el comparador de migración. Los resultados siguientes describen comprobaciones históricas; los comandos de referencia y comparación ya no están disponibles en el árbol actual.

Comandos actuales: `clojure -M:test`, `clojure -M:test lyrics` y `python3 -m unittest discover -s legacy/tests -p test_whisper_worker.py -v`.

Tras reorganizar las fuentes, se ejecutaron de nuevo las 17 pruebas Clojure (583 aserciones) y las 3 pruebas del adaptador Python: todas aprobadas. Una transcripción nueva con Whisper `base` en CPU, intervalo 38–44 s de `vocals.wav`, cargó el adaptador desde `legacy/` y generó 2 eventos y 23 unidades en `/tmp/karaoke-legacy-layout-smoke-20261107/`. `git diff --check` también pasó.

---

# Validación de la versión 0.2.0 — Clojure/JVM

Realizada el 7 de noviembre de 2026, con Java 21, Clojure 1.12.4 y data.json 2.5.2. La documentación de dependencias se consultó mediante Context7. La CLI y todo el procesamiento de la aplicación están en Clojure; Whisper/PyTorch conservan un adaptador Python limitado a inferencia.

- `clojure -Srepro -M:test`: 17 pruebas y 583 aserciones aprobadas. Incluyen decodificación FFmpeg real con audio sintético, regeneración, sílabas, tiempos aleatorios, efectos, offsets, vista previa, correcciones conservadoras, reintentos, propiedad de ventanas, checkpoints y exclusión de otro proceso JVM mediante el bloqueo de salida.
- `clojure -Srepro -M:test lyrics`: 1 prueba y 25 aserciones aprobadas, sin FFmpeg ni red.
- `python3 -m unittest discover -s tests -p test_whisper_worker.py -v`: 3 pruebas aprobadas del adaptador nuevo. Verifican liberación de referencias tras OOM en carga/transcripción, permanencia en CPU, CUDA explícito estricto y propagación de errores ajenos a OOM.
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`: 35 pruebas aprobadas de referencia y adaptador.
- `PYTHONPATH=src .venv/bin/python scripts/check_parity.py`: 18 casos aprobados (efectos k/kf/ko, offsets −0,65/0/+0,19 y unidades palabra/sílaba). ASS, TXT y TSV coinciden byte por byte con la referencia Python; los JSON de transcripción son equivalentes. También coinciden los códigos, tiempos y textos de los avisos; sus mensajes pueden estar redactados de forma distinta.
- Prueba real sobre `vocals.wav`, intervalo 38–44 s, Whisper `base`, CPU, cuatro hilos y `--no-retry`: 2 eventos, 23 unidades y 7 avisos. Se generaron los ocho archivos de salida y el checkpoint en `/tmp/karaoke-clojure-voice-20261107/`. Una segunda ejecución reutilizó el checkpoint sin cargar el modelo. La referencia Python también reutilizó ese checkpoint y produjo ASS/TXT/TSV idénticos.
- Un checkpoint Python antiguo, sin `retry_enabled` y con números enteros donde Clojure usa decimales, se reutiliza en la prueba automatizada de compatibilidad.
- Se ejecutó el launcher desde otra carpeta, con rutas relativas y espacios y `KARAOKE_PYTHON=/nonexistent/python`: `--from-json` generó los archivos correctamente, sin depender de Python.
- `bash -n karaoke-ass` y `git diff --check`: aprobados.

En esta sesión se seleccionó Java directamente en PATH para evitar que el shim local de mise intentara escribir fuera del entorno permitido. No se agotó la memoria de una GPU real ni se verificaron de oído la letra o los límites silábicos. El ejemplo sigue siendo un borrador; las pruebas demuestran generación, compatibilidad y coherencia temporal. Las consultas LRCLIB se simularon: no se hizo una consulta real al catálogo.

El primer intervalo probado, 18–24 s, resultó silencioso: se conservó el JSON original de diagnóstico y no se generó un ASS vacío. Los medios y las transcripciones locales del usuario no se modificaron.

---

# Validación de la versión 0.1.1

Realizada el 20 de octubre de 2026.

- 24 pruebas aprobadas. Se añadieron seis pruebas de recuperación ante falta de memoria: carga parcial del modelo, transcripción, reintentos, liberación de referencias GPU, conservación del checkpoint y comportamiento con CUDA explícito, CPU y errores ajenos a la memoria.
- Se simularon excepciones de CUDA y se verificó que `--device auto` vuelve a intentar el mismo trabajo en CPU una sola vez y conserva el mismo modelo. Los tramos siguientes permanecen en CPU.
- Una ejecución real en CPU usando el entorno `.venv` del proyecto (PyTorch 2.14.1) convirtió un fragmento de seis segundos con Whisper base y produjo un ASS con dos eventos y 15 unidades.
- No había acceso a CUDA desde la sesión de validación. La recuperación GPU se verificó mediante simulación, no agotando la memoria de una GPU real.
- La versión 0.1.1 no modifica el algoritmo de reconocimiento ni los cortes silábicos; conserva sus límites de precisión.

---

# Validación de la versión 0.1.0

Realizada el 20 de octubre de 2026.

- 18 pruebas automáticas aprobadas: separación textual, límites de palabras y sílabas, cuantización, pausas, desplazamientos, duplicados, reanudación, reintentos, bloqueos y regeneración.
- Se construyó un paquete wheel, se instaló en una carpeta aislada y se ejecutó su comando de versión y una conversión desde el JSON de la conversación anterior.
- Se ejecutó el reconocimiento real de toda la pista vocal original: 187,977 s de audio decodificado, Whisper medium, CPU, cuatro hilos, idioma automático y prompt de nombres propios.
- La primera pasada produjo ocho ventanas. Se hicieron cuatro reintentos automáticos; se eligió el segundo resultado en tres ventanas. En 24–48 s, la cobertura de señal activa pasó del 3,4 % al 93,3 %; en 96–120 s, del 25,3 % al 95,1 %; en 120–144 s, del 1,0 % al 63,0 %.
- El resultado final tiene 45 eventos y 385 unidades de karaoke. Se comprobó que los tiempos crecen, que no hay eventos superpuestos y que las etiquetas de karaoke suman la duración de cada evento.
- Se repitió la ejecución con el checkpoint completo: reutilizó las ocho ventanas y generó los resultados sin volver a cargar el modelo.
- FFmpeg/libass renderizó el ASS; se inspeccionó una imagen de la primera frase para comprobar fuente, posición y resaltado.

## Límites observados

El resultado real conserva errores de reconocimiento, especialmente en el rap y el cierre en inglés. Detectar un idioma por ventana no resuelve un cambio de idioma dentro de la misma ventana. La cobertura por energía permite señalar posibles omisiones, pero no verifica las palabras.

El informe del ejemplo contiene 57 avisos: 40 de confianza baja, 8 regiones con energía sin texto, 4 reparaciones temporales, 2 ventanas con cobertura baja, 2 palabras detectadas en silencio y 1 duplicado superpuesto eliminado. Los tramos 120–144 s y 168–187,98 s siguen requiriendo revisión.

No se ha verificado de oído la letra completa ni los límites silábicos. Las pruebas confirman la generación y coherencia temporal del borrador; no una transcripción exacta ni una alineación fonética.

El ejemplo se entrega en la carpeta vecina `karaoke-ass-ejemplo`. Las métricas anteriores describen esa ejecución concreta y no son una garantía para otras canciones.
