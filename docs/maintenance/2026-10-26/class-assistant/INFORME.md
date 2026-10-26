# Voz, parada y arranque de Reachy

Cambios instalados el 26 de octubre de 2026, después de revisar las aplicaciones y los registros del robot.

## Funcionamiento

- Clase continua recibe el audio natural de ElevenLabs por paquetes, sin esperar a descargar cada parte completa. Conserva el resumen antes de leerlo y mantiene la parada existente, ahora rotulada «Parar habla». La palomita tiene en cuenta el estado del reconocimiento y el retraso pendiente. El botón sigue confirmando el texto antes de resumir; nunca omite transcripciones para ganar velocidad.
- El asistente usa voz progresiva, conexión HTTP reutilizada entre fragmentos y una cola de audio limitada. «Parar habla» invalida el audio y el texto pendientes, cancela la respuesta del modelo y permite una conversación posterior. La palomita indica conexión y disponibilidad para escuchar, porque esta aplicación responde automáticamente.
- Se conserva la voz y el modelo elegidos en el asistente, los proveedores alternativos y el efecto robótico. Este último conserva el procesamiento por frase completa para no alterar su timbre; no obtiene la misma mejora de inicio que las voces naturales.
- Si ElevenLabs falla después de empezar a leer una clase, no vuelve a leer todo el resumen mediante otra voz. El resumen permanece guardado y se informa del fallo.
- Se corrige la pérdida de espacios entre fragmentos de texto recibidos por el asistente.

## Arranque y acceso

El registro mostraba `wake_up_on_start=False` y los nueve motores detectados correctamente. El lanzador oficial incluía `--no-wake-up-on-start`. Se instaló un complemento de systemd que usa `reachy-start-awake.sh` para cambiar únicamente esa opción al ejecutar el lanzador del fabricante, sin editar el paquete instalado.

Archivos instalados:

- `/home/pollen/reachy-start-awake.sh`
- `/etc/systemd/system/reachy-mini-daemon.service.d/wake-on-start.conf`

Tras reiniciar el servicio, el registro mostró `wake_up_on_start=True`, el controlador quedó disponible, la API confirmó motores `enabled` y el asistente arrancó automáticamente. Se probó el reinicio del servicio, no un corte eléctrico completo.

El portal no estaba eliminado: el router había asignado al robot `192.168.1.100`, mientras que `192.168.1.101` correspondía al computador. El navegador confirmó cinco aplicaciones visibles sin errores en `http://192.168.1.100:8090/`. También se observó resolución intermitente de `reachy-mini.local` desde Windows; no se modificó la configuración del router ni se garantiza que DHCP conserve la dirección.

## Validación

- 8 pruebas específicas de voz, cancelación y conservación de paquetes pasaron en el entorno del robot. Incluyen parar con audio en cola y volver a emitir otro turno.
- 40 pruebas existentes de clase continua pasaron, con los proveedores externos simulados.
- Comprobación de sintaxis Python y JavaScript; coincidencia exacta de los ocho archivos desplegados con los archivos probados.
- Prueba hablada breve del asistente: la petición de parada respondió en 0,043 segundos y el estado volvió a `idle`. Ese tiempo mide la respuesta de control, no una medición acústica del altavoz.
- Portal comprobado desde navegador: cinco tarjetas visibles y cero errores de JavaScript.
- Las pruebas anteriores del Hub y de reconocimiento se documentan en las carpetas contiguas.

Las latencias de voz dependen del servicio, la red, el texto y el efecto elegido. No se promete respuesta instantánea ni se ha validado una clase larga completa en esta intervención.

## Respaldo y publicación

Respaldo del robot: `/home/pollen/class-assistant-backups/20261026-140613`. `deployment.json` registra las huellas originales y finales; `changes.patch` conserva el cambio exacto sobre la instalación existente. La instalación se verificó después de una interrupción de conexión durante el despliegue.

Para deshacer el cambio de arranque, retirar únicamente el complemento `wake-on-start.conf`, recargar systemd y reiniciar el servicio cuando no haya una clase en curso. Los originales de las aplicaciones están en el respaldo indicado.

Git incluye el asistente, las mejoras del Hub, la clase continua actual y el portal actual, con sus funciones previas de movimientos, cámara y control. Los archivos de configuración privados, claves, grabaciones y transcripciones no se publican. Las dependencias declaradas del asistente se completaron para reflejar las importaciones actuales; una instalación nueva sigue requiriendo el entorno ARM64 y las credenciales locales.
