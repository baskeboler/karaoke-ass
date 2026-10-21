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
