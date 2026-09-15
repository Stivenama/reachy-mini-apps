# Asistente de clase

Aplicación local que trabaja con los TXT de Clase continua. Incluye interfaz web, gestión de cuentas, subida a Drive, materiales de Classroom y generación de informes, infografías y videos con NotebookLM. Conserva las opciones de procesamiento y publicación de la aplicación original.

Se ejecuta en el computador Windows, por separado del robot. La integración con NotebookLM utiliza `notebooklm-py`, una herramienta no oficial; su sesión se autoriza separadamente de Drive y Classroom.

## Instalación en Windows

Con Python 3.12 instalado, abrir una terminal en esta carpeta:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item config.example.json config.json
New-Item -ItemType Directory -Force secrets,transcripciones
```

Las dependencias registran las versiones del entorno que funciona en el computador original. La reinstalación completa en otro computador todavía debe comprobarse.

## Conectar las cuentas

1. Preparar un proyecto propio de Google Cloud con Drive API y Classroom API habilitadas, consentimiento OAuth y cliente de aplicación de escritorio.
2. Guardar el archivo OAuth descargado como `secrets/client_secret.json`, únicamente en el computador. Si el proyecto está en pruebas, incluir la cuenta en sus usuarios de prueba.
3. Abrir `iniciar_asistente.bat`. La interfaz se abre en `http://127.0.0.1:8100`.
4. Usar **Agregar cuenta** para iniciar sesión en NotebookLM y autorizar Drive/Classroom. También está disponible `agregar_cuenta.bat`.

Cada instalación necesita su propia autorización. Los perfiles, tokens y sesiones del computador original no se incluyen.

## Recibir los TXT de Reachy

Ejecutar `iniciar_guardar_transcripcion.bat` y mantener su ventana abierta. El receptor escucha en `127.0.0.1:8765`, donde la web de Clase continua envía los textos; guarda los archivos en `transcripciones/` dentro de esta carpeta. Abrir la web del robot desde ese mismo computador.

El asistente lee esa carpeta y permite seleccionar la clase y sus opciones de procesamiento. Si se conservan los TXT en otra ubicación, ajustar `transcripts_dir` en `config.json` y la variable `GUARDAR_DIR` del receptor a la misma ruta absoluta. No ejecutar dos receptores simultáneamente en el puerto 8765. Para detener el receptor, pulsar Ctrl+C en su ventana.

## Archivos que permanecen privados

`secrets/`, `config.json`, `data/`, `descargas/`, `transcripciones/`, registros y el entorno `.venv/` están excluidos. NotebookLM puede guardar su sesión fuera de esta carpeta: tampoco se debe subir. El prototipo antiguo con una clase y un identificador real de Classroom se omite; la aplicación actual incluye esas funciones de forma configurable.

Se conserva la tipografía estática Ancizar Sans de la interfaz original; ver también la nota de procedencia del repositorio. La copia publicada normaliza las rutas de transcripciones y los ejemplos de cuentas; no modifica la aplicación instalada.

## Validación de esta copia

Se comprobó la sintaxis Python y, en una copia temporal sin cuentas, la carga de la página, JavaScript, estilos, configuración y listado de clases. Un TXT ficticio guardado por el receptor apareció en el listado del asistente. No se ejecutaron trabajos de generación ni publicaciones en servicios externos, ni se verificó el arranque del trabajador de tareas con trabajos reales. Se revisaron los archivos antes de publicarlos para excluir credenciales y datos privados.
