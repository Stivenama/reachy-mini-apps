# Optimización de latencia — 26 de octubre de 2026

Se instalaron cinco archivos corregidos en el robot. Clase continua se reinició estando inactiva y volvió a responder sin errores. Hub y Asistente cargarán sus cambios la próxima vez que se abran.

## Cambios

- **Asistente:** retira continuamente muestras del micrófono mientras habla y durante la protección contra eco. Antes dejaba de leerlas, permitiendo que quedara audio antiguo pendiente. Se conserva la protección contra eco de 2,5 segundos.
- **Asistente:** elimina la espera artificial de 20 ms por muestra cuando ya hay audio disponible; espera 5 ms cuando no lo hay. Los botones de hablar/saludar ejecutan la síntesis fuera del bucle de solicitudes. Cierra el cliente de conexión al desconectarse y limpia la señal de conexión.
- **Hub:** reutiliza conexiones HTTP de ElevenLabs entre solicitudes. Cuando no hay muestras de micrófono, espera 5 ms en vez de consultar continuamente.
- **Clase continua:** con dos segundos o más pendientes, omite guardar hipótesis provisionales reemplazables; sigue guardando todos los resultados finales. Esto evita que escrituras repetidas de borradores bloqueen la lectura de progreso del transcriptor. Con ocho segundos pendientes reduce la frecuencia de cálculo de vistas previas, conservando el audio, el modelo y las frases completas.
- **Clase continua:** al cambiar de bloque confirma el texto anterior incluso si el audio termina exactamente en un límite de paquete.

No se cambió la voz elegida, el modelo de español, las credenciales, la cámara, el control de movimientos, el portal ni las transcripciones existentes. La cola de seguridad conserva su límite original de 120 segundos; no se reduce ocultando o descartando audio.

## Evidencia y límites

- 47 pruebas automáticas aprobadas sobre el código candidato en el robot: 40 existentes y 7 nuevas. Cubren audio, sesiones, cámara, validación del control, recuperación del transcriptor y los cambios del micrófono/conexiones. Las nuevas pruebas de micrófono usan muestras simuladas y no constituyen una conversación física completa.
- Modelo real Vosk, audio existente procesado exclusivamente dentro del robot: una cola inicial de **40 s** se procesó en **21,36 s**, quedó en **0 s** y confirmó todos los bytes aceptados. Sin reproducción ni exportación del contenido.
- Este ensayo introduce una cola de golpe. **No reproduce ni demuestra resuelto el incidente intermitente de una clase larga**, ni mide exactitud contra una transcripción humana.
- Comparación previa: 43,024 s de audio requirieron aproximadamente 22 s. Reducir siempre la renovación del reconocedor de 60 a 15 s empeoró la prueba continua de 120 s (52,84 frente a 69,56 s) y cambió el texto; ese ajuste se descartó. Cambiar el tamaño de paquete tampoco aportó mejora clara y no se aplicó.
- Verificación posterior: clase continua HTTP 200, sin error ni atraso; cámara JPEG válida HTTP 200; estado del control HTTP 200; portal HTTP 200 con cinco aplicaciones; daemon en ejecución.
- No se midió una mejora porcentual de conversación completa con Hugging Face/ElevenLabs. La latencia de esos proveedores y la espera del Hub por el texto final con voz personalizada siguen influyendo.
- No se ejecutaron movimientos físicos ni una clase completa prolongada. No se puede garantizar ausencia absoluta de errores a partir de estas pruebas.

## GitHub y versiones

El HEAD remoto de `Stivenama/reachy-mini-apps` coincidía con el local: `c03d1f78a457afdaa678dd758e13ae701bfa63bc`. No había una revisión remota nueva que incorporar.

La instalación del robot tiene funciones posteriores que aún no están completas en este repositorio, especialmente voz compartida y Asistente del robot. Se sincronizaron los dos archivos de clase continua compatibles y se añadieron cuatro pruebas. Los cambios de la instalación se conservan completos en `changes.patch`, relativo a `/home/pollen/apps`, junto con los hashes de cada versión en `deployment.json`. **No aplicar todo ese parche directamente sobre las carpetas antiguas del repositorio.**

Los cambios quedan locales y no se han publicado ni enviado a GitHub.

## Respaldo y reversión

Respaldo exacto del robot: `/home/pollen/latency-backups/20261026-103315`.

Respaldo local: `C:/Users/Fundicion/Documents/reachy/latency_work/originals-20261026-103315.zip`.

El manifiesto identifica los cinco archivos y sus hashes anteriores/posteriores. Para revertir, detener primero cualquier clase o conversación, restaurar exclusivamente esos cinco archivos desde el respaldo y volver a iniciar la aplicación. El instalador verificó que los originales no hubieran cambiado y realizó reemplazos atómicos; tenía reversión automática si clase continua no regresaba saludable.

Scripts y pruebas de mantenimiento se conservan en `C:/Users/Fundicion/Documents/reachy/latency_work`, con el instalador `deploy_latency.py` en su carpeta superior. El instalador está protegido por hashes y no debe reutilizarse como actualización genérica.
