# Reachy Mini — aplicaciones y entorno

Desarrollo organizado para mantener y reconstruir las aplicaciones de un Reachy Mini Wireless con Linux ARM64 y Python 3.12.

| Carpeta | Contenido |
| --- | --- |
| `apps/reachy_mini_hub` | Hub de conversación, movimientos, personalidades y herramientas; incluye adaptaciones locales. |
| `apps/reachy_mini_audio_mixer` | Selección y control de salida de audio. |
| `apps/reachy_mini_classroom` | Clase continua: transcripción local en español, bloques de 20 minutos y resumen hablado bajo demanda. |
| `portal` | Controlador web que inicia y abre las aplicaciones. |
| `environment` | Versiones instaladas y listas para reconstruir los entornos. |
| `scripts` | Preparación de entornos y revisión de archivos antes de publicar. |
| `docs` | Instalación, arquitectura y manejo de datos privados. |

El entorno virtual se conserva como instrucciones y dependencias versionadas. Los ejecutables de un `venv` contienen rutas y dependen del sistema donde fueron creados; se reconstruyen en el equipo de destino, según la [documentación de Python](https://docs.python.org/3/library/venv.html#how-venvs-work).

## Empezar

1. Leer [instalación](docs/INSTALACION.md).
2. Crear los entornos con `bash scripts/create_environments.sh` en Linux ARM64 con Python 3.12.
3. Configurar las credenciales únicamente en archivos locales a partir de `.env.example`.
4. Seguir la integración con el daemon y el portal descrita en la guía. Crear entornos por sí solo no registra las aplicaciones en el controlador del robot.

La publicación no incluye cuentas, tokens, redes WiFi, bases de datos, transcripciones, recuerdos personales, audios ni el respaldo privado del robot. Incluye un perfil genérico en español en lugar de las personalidades privadas.

## Comprobaciones

```bash
python scripts/check_publication.py
python -m unittest discover -s apps/reachy_mini_classroom/tests -p 'test_*.py' -v
```

Las pruebas unitarias no necesitan hablar por el robot ni enviar clases a proveedores. Las pruebas de micrófono, movimiento y reproducción se hacen aparte en el hardware. La reconstrucción completa de todas las dependencias desde Internet debe validarse en un entorno limpio ARM64: los inventarios registran las versiones observadas, pero no garantizan que todos los paquetes sigan disponibles para cualquier plataforma.

Ver [privacidad](docs/PRIVACIDAD.md) y [procedencia del código](THIRD_PARTY.md).
