# Reachy Hub: voz progresiva, indicador y parada

Instalado el 26 de octubre de 2026 en el robot. Alcance: exclusivamente Reachy Hub. La voz seleccionada, el modelo ElevenLabs configurado, las voces nativas y las demás aplicaciones se conservan.

## Funcionamiento corregido

Antes, el Hub esperaba la transcripción final de toda la respuesta y luego descargaba el audio ElevenLabs completo antes de reproducirlo. Además, iniciaba hilos independientes para texto y transcripción, con deduplicación por texto/tiempo.

Ahora consume los fragmentos de texto del modelo, agrupa frases cortas y solicita PCM de 16 kHz al endpoint de transmisión progresiva. Un productor prepara el audio mientras otro lo reproduce en orden. Se conserva la conexión HTTP, se limita la cola y se evita duplicar el texto enviado por los dos canales del modelo. Un proveedor que solo entrega texto final sigue siendo compatible, aunque no permite anticipar la síntesis durante la generación del texto.

La palomita utiliza el estado de conexión y de respuesta, el audio recibido, el silencio o una transcripción estable. Así no depende exclusivamente de que el ruido baje de un umbral fijo. La pantalla recupera el estado una vez por segundo y al recargarse. Las notificaciones nuevas de texto retiran el indicador mientras continúa el reconocimiento. La palomita indica que se puede enviar el turno; no es una garantía de disponibilidad futura del proveedor.

«Parar habla» bloquea la salida inmediatamente, cancela la síntesis externa y la respuesta del modelo, vacía el reproductor y las solicitudes pendientes, y rechaza eventos atrasados de respuestas canceladas. También protege el mecanismo alternativo de voz nativa para que un error posterior a la parada no reinicie el habla.

## Pruebas

- **20 pruebas Python aprobadas** en el entorno del robot: transmisión antes del texto final, orden y conservación de bytes PCM, límites de paquetes impares, cancelación durante espera de red, voz/modelo seleccionados, indicador con ruido, desconexión, respuesta ocupada, parada de colas, eventos tardíos y pruebas anteriores de estabilidad/movimientos.
- Navegador aislado: palomita después de perder una notificación, recarga de página, retirada del indicador al continuar hablando, estado Pensando, parada durante generación y habla, sin errores de JavaScript. Diseño comprobado a 430 px de ancho.
- Solicitudes reales a ElevenLabs con texto sintético y la configuración actual, sin guardar audio: descarga completa **1,247 s**; primer fragmento de transmisión **0,249 s**; transmisión completa **0,596 s**. Son dos solicitudes individuales; no representan un promedio ni una mejora garantizada para todas las respuestas.
- Prueba real por el Hub y el parlante, usando una frase sintética: primera reproducción notificada **1,151 s** después de solicitarla; respuesta al RPC de parada **0,018 s**; ningún evento de audio nuevo después del margen de 300 ms durante la observación de 2,5 s.
- La medición de parada es el tiempo de respuesta del control, no una medición acústica del altavoz. La latencia restante depende del modelo, la red y ElevenLabs; no se promete respuesta instantánea.
- Se comprobó el Hub conectado y sirviendo la interfaz actualizada. La última protección adicional de cancelación nativa pasó las pruebas antes de su instalación.

## Archivos y recuperación

Cinco archivos instalados: `console.py`, `huggingface_realtime.py`, `elevenlabs_stream.py` (nuevo), `static/js/api.js` y `static/js/views/talk.js`, bajo `/home/pollen/apps/reachy_mini_hub/src/reachy_mini_hub/`.

Respaldo original: `/home/pollen/hub-voice-backups/20261026-134054`. Copia local: `C:/Users/Fundicion/Documents/reachy/hub_voice_work/originals-20261026-134054.zip`. Los hashes exactos están en `deployment.json` y el cambio completo en `changes.patch`.

Para revertir, hacerlo con Hub inactivo: restaurar los cuatro archivos anteriores del respaldo y retirar el módulo nuevo del directorio de importación; después reiniciar Hub. No restaurar configuraciones ni aplicaciones ajenas. El primer intento de instalación se revirtió porque el controlador representa «sin aplicación» como `null`; el instalador se corrigió y la instalación final se completó sobre Hub inactivo.

Las fuentes y pruebas de trabajo quedan en `C:/Users/Fundicion/Documents/reachy/hub_voice_work`. El parche está referido a la instalación actual del robot, que contiene funciones posteriores a la copia antigua de Hub en este repositorio; no aplicarlo sin comprobar los hashes de origen. No se realizó publicación ni envío a GitHub.

## Documentación consultada

La estrategia de transmitir audio progresivamente y reducir la espera por fragmentos sigue la [guía oficial de latencia de ElevenLabs](https://elevenlabs.io/docs/eleven-api/guides/how-to/best-practices/latency-optimization). El endpoint, PCM y contexto entre fragmentos se verificaron en [Stream speech](https://elevenlabs.io/docs/api-reference/text-to-speech/stream). No se activaron opciones obsoletas que reduzcan calidad o deshabiliten la normalización de números.
