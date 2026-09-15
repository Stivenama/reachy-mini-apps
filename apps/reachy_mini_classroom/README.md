# Clase continua 0.2 — Reachy Mini

Aplicación del portal en el puerto 8093. Escucha y transcribe localmente en español usando Vosk; utiliza el servicio configurado del Hub para resumir y hablar, sin requerir otra clave.

## Uso

1. Abre **Clase continua** e inicia una clase. El recuadro **Transcripción en vivo** muestra hipótesis parciales y frases confirmadas entre silencios.
2. Cada 20 minutos espera el siguiente silencio de 1,5 segundos, cierra el bloque y continúa escuchando. El micrófono no se detiene al cambiar de bloque. El reconocedor se vacía en orden y comienza un contexto nuevo.
3. **Pausar escucha** termina la frase pendiente y guarda el texto sin hablar. Puedes continuar la misma clase.
4. **Responder** finaliza la escucha y resume todos los bloques en español. El audio se reproduce a medida que llega.
5. **Pausar voz** cancela la generación y detiene el reproductor. El resumen escrito se conserva; volver a pulsar Responder lo lee desde el principio.
6. **Descargar texto** exporta todos los bloques en orden.

La aplicación reutiliza MovementManager y el movimiento reactivo al audio del Hub: comportamiento de escucha, respiración en reposo y movimiento al hablar. Solo Responder activa la voz.

## Almacenamiento

Se recomienda SQLite local: `/home/pollen/recordings/classroom/classes.sqlite3`. Guarda texto, bloques, duración y resumen con transacciones. El audio de entrada y salida solo pasa por memoria: esta versión no crea PCM, WAV ni otros archivos de audio. Los archivos históricos de la versión anterior no se borran automáticamente.

Los parciales actualizan la misma fila, evitando concatenar hipótesis acumulativas. Se conservan las repeticiones que sí pertenecen a intervenciones diferentes. Tras un reinicio se recupera el texto escrito; el audio aún no reconocido en memoria no puede recuperarse. La aplicación no reanuda sola ni escucha ni voz.

## Reconocimiento y rendimiento

Modelo local español `vosk-model-small-es-0.42`, independiente del reconocedor remoto del Hub. No necesita internet para transcribir. El modelo pequeño funciona en el robot, pero puede equivocarse con ruido y vocabulario técnico: revisar el texto en vivo. Los textos históricos no se corrigen retroactivamente.

La cola de audio es limitada; si el motor no alcanza el ritmo, se pausa y muestra un error. Internamente también reinicia el contexto tras 60 segundos sin finalizar una frase, evitando crecimiento indefinido. Los bloques visibles mantienen el ciclo de 20 minutos y siguiente silencio. Ruido constante puede retrasar ese cierre.

El resumen y la voz necesitan internet. Para clases largas se resumen grupos y se integran sus síntesis, incluyendo todos los bloques; la exportación conserva el texto completo. Responder ya no vuelve a transcribir grabaciones ni genera voz durante la etapa de resumen.

## Instalación

En el robot, ejecutar `scripts/install_spanish_asr.py` con `/venvs/apps_venv/bin/python`. Crea un entorno dedicado y descarga el modelo oficial español sin cambiar dependencias del Hub.

```bash
/venvs/apps_venv/bin/python scripts/install_spanish_asr.py
/venvs/apps_venv/bin/python -m pip install --no-deps --no-build-isolation -e /home/pollen/apps/reachy_mini_classroom
python -m unittest discover -s tests -v
```

Registrar `reachy_mini_classroom` en el portal, URL `http://127.0.0.1:8093`. El script externo `deploy_classroom.py` actualiza ese registro y conserva una copia previa. Variables opcionales: `CLASSROOM_DATA_DIR`, `CLASSROOM_VOICE`, `CLASSROOM_ASR_PYTHON`, `CLASSROOM_SPANISH_MODEL`, `CLASSROOM_SUMMARY_CHARS`, `CLASSROOM_SUMMARY_WORDS`, `CLASSROOM_MAX_OUTPUT_TOKENS` y `CLASSROOM_SPEECH_CHARS`. Entrada/salida PCM: 16 kHz.

## Sesiones de respuesta (0.2.1)

La captura y el cierre de bloques no llaman a Pollen. El botón Responder abre una sola sesión, compartida por las síntesis intermedias, el resumen final y la voz. Cada solicitud lleva su propio texto sin acumular un historial ilimitado. Se cierra al terminar, cancelar o fallar. Si ya hay resumen guardado, se reutiliza.

Un 429 en el asignador ocurre antes de enviar el texto: es un límite de solicitudes, no un diagnóstico de longitud del texto. La aplicación respeta Retry-After y limita los reintentos de la apertura necesaria; nunca reintenta durante la escucha. No se puede anular desde la app una cuota impuesta por el proveedor.

Los registros CLASSROOM START_CLASS, VOSK_STARTED, TRANSCRIPT_CONFIRMED, BLOCK_SAVED y SESSION_REQUEST permiten auditar la separación. /api/status incluye remote_session_requests. Un bloqueo adicional rechaza sesiones fuera del estado de respuesta con captura terminada.

## Audio continuo (0.2.2)

La recepción del WebSocket y la reproducción usan tareas independientes. El receptor coloca PCM en una cola de RAM limitada por un máximo de 10 minutos de audio (19,2 MB). No espera a que el altavoz termine cada paquete; así puede seguir procesando los mensajes de red y sus comprobaciones de conexión.

La reproducción acumula inicialmente 350 ms, entrega paquetes de 20 ms y mantiene unos 300 ms de margen en el mezclador. Al terminar espera a que salga la cola antes de detener el dispositivo. Pausar voz cierra la salida e impide que un callback pendiente vuelva a activarla. No se guardan archivos de audio.

## Tiempo de respuesta (0.2.3)

Cada grupo de texto cuesta una petición secuencial sobre la misma sesión. El troceado anterior usaba 12.000 caracteres con cortes de 10.000, lo que dejaba grupos a medio llenar y generaba unas 17 peticiones para una clase de dos horas. Ahora los grupos se llenan hasta `CLASSROOM_SUMMARY_CHARS` (40.000 por omisión) y un texto solo se parte cuando él mismo excede el presupuesto: la misma clase cuesta 5 peticiones y las de hasta ~40.000 caracteres se resuelven en una sola pasada.

El presupuesto depende de la ventana de contexto que acepte la sesión del Hub. Si aparecen errores de longitud, bajarlo con esa variable; para medir el efecto, comparar las marcas de tiempo de `CLASSROOM RESPOND_BEGIN` y `CLASSROOM SUMMARY_READY`. El registro `CLASSROOM SUMMARY_LEVEL` indica cuántos grupos quedan en cada nivel. El mínimo efectivo es cuatro notas por grupo; por debajo la reducción no convergería y una comprobación aborta en lugar de repetir el nivel.

Durante la espera, `/api/status` publica `progress_note` y la interfaz muestra la etapa en curso: conexión, «Resumiendo parte N de M», redacción final y lectura en voz alta.

## Lectura completa (0.2.4)

La versión anterior podía recibir respuesta completed aunque la voz terminara antes del último párrafo. Se reprodujo con un texto sintético: 140,352 segundos de audio y una cola que no contenía el final solicitado.

Ahora se generan partes de hasta 900 caracteres, preferentemente entre frases y sin partir palabras. Todas usan la misma conexión y el mismo reproductor. El test con el mismo texto produjo 168,672 segundos, y la transcripción LOCAL del audio final confirmó la última oración de control completa. No es un límite universal documentado del proveedor: es el fallo observado y una solución comprobada para esta instalación.

Se mantienen los resúmenes guardados íntegros. Para resúmenes nuevos, CLASSROOM_SUMMARY_WORDS fija el objetivo de extensión y se permiten reescrituras para aproximarse a él; si todavía se supera, no se borran las frases finales. Los números y las unidades se desarrollan en palabras antes de hablar. CLASSROOM_SPEECH_CHARS ajusta el tamaño de las partes; CLASSROOM_MAX_OUTPUT_TOKENS configura el tope solicitado al servicio.

La pantalla muestra el progreso de generación por partes. SESSION_REQUEST permite verificar que solo se asigna una sesión; SPEECH_PART_BEGIN, SPEECH_PART_DONE y SPEECH_DONE registran la cobertura de todas las partes, sin guardar audio. La salida conserva un máximo de diez minutos en RAM. Pausar voz cancela la lectura y vacía el reproductor.
