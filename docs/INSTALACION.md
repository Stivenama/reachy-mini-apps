# Instalación y recuperación del desarrollo

## Sistema de destino

La instalación de referencia usa Reachy Mini Wireless, Linux ARM64, Python 3.12 y SDK Reachy Mini 1.10.0. El daemon, PipeWire/PulseAudio, GStreamer y los permisos del hardware deben estar disponibles desde la imagen compatible del robot. Este repositorio no reinstala su sistema operativo.

`environment/*-installed.json` registra los paquetes observados. Las listas `*-requirements.txt` permiten reconstruir las dependencias con versiones fijas. El entorno general original contiene otras aplicaciones: se excluyen sus paquetes de las listas instalables cuando no forman parte de este repositorio; sus nombres quedan documentados en el inventario. Las versiones son una instantánea, no una resolución nueva probada desde cero.

Algunos paquetes nativos (PyGObject, pycairo, audio y visión) pueden requerir bibliotecas de desarrollo de la imagen del robot. PyTorch CPU utiliza su índice oficial además de PyPI. Si falta una rueda ARM64 o una versión deja de estar disponible, revisar el inventario y la compatibilidad antes de cambiar versiones; no actualizar automáticamente el entorno del robot en uso.

## Preparación aislada

```bash
REACHY_PYTHON=python3.12 bash scripts/create_environments.sh
```

Esto crea `.venvs/apps`, `.venvs/asr` y `.venvs/portal` dentro del repositorio. No los subir a Git. Para escuchar con el entorno de desarrollo, definir `CLASSROOM_ASR_PYTHON` con la ruta absoluta de `.venvs/asr/bin/python`. Para usar la configuración privada, editar `.env` localmente o autenticar Hugging Face de forma local. No copiar tokens a archivos versionados.

El perfil predeterminado del Hub es genérico y está empaquetado en `src/reachy_talk_data/profiles/default`. Las personalidades originales y recuerdos no se incluyen.

## Instalar en el controlador existente

Conservar el respaldo privado antes de modificar una instalación funcional. Cerrar las aplicaciones desde el portal y usar el entorno que el daemon emplea para sus aplicaciones (en la instalación de referencia, `/venvs/apps_venv`). Desde la raíz del repositorio:

```bash
/venvs/apps_venv/bin/python -m pip install --no-deps --no-build-isolation \
  -e ./apps/reachy_mini_hub \
  -e ./apps/reachy_mini_audio_mixer \
  -e ./apps/reachy_mini_classroom
/venvs/apps_venv/bin/python apps/reachy_mini_classroom/scripts/install_spanish_asr.py
```

La opción `--no-deps` reutiliza las dependencias existentes del robot. En una instalación nueva hay que prepararlas primero con las listas versionadas y verificar sus requisitos nativos. El instalador español descarga el modelo Vosk oficial y crea el entorno aislado de reconocimiento bajo `~/apps`; no copia audios ni clases.

## Portal

Copiar `portal/portal_config.example.json` a `portal/portal_config.json` localmente y adaptar solo los registros necesarios. Los puertos de las aplicaciones administradas por el daemon son Hub 7863, Audio Mixer 7862 y Clase continua 8093. El Hub ejecutado directamente por su CLI usa 7860.

```bash
cp -n portal/portal_config.example.json portal/portal_config.json
.venvs/portal/bin/python portal/launcher.py
```

El portal usa el daemon local en el puerto 8000 y escucha en 8090. Para autoinicio, adaptar la unidad de ejemplo de `portal/README.md` a la ruta del repositorio y al intérprete del entorno de portal elegido. No reemplazar la configuración de servicios del equipo sin conservarla antes.

## Datos y comprobaciones

Las clases nuevas se guardan localmente en SQLite; la base vacía se crea al iniciar la aplicación. Para recuperar clases anteriores se utiliza el respaldo privado, con la aplicación detenida; ninguna clase real procede de GitHub.

Comprobar apertura de las tres aplicaciones, selección de altavoz, escucha en español, cierre de bloques tras silencio, resumen bajo demanda, pausa de voz y movimientos. Las pruebas unitarias incluidas verifican partes de esa lógica con datos sintéticos; no sustituyen la prueba del hardware ni una instalación limpia de dependencias.
