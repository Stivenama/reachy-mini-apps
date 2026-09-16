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

### Investigación académica basada en el TXT

La búsqueda lee directamente el TXT local completo en fragmentos de hasta 12.000 caracteres y envía esos fragmentos como texto de la consulta. Extrae los temas de cada fragmento y los agrupa en hasta 6 ejes. Así no depende de que NotebookLM recupere pasajes de la fuente remota. Si falla un fragmento, informa del fallo en vez de investigar con una transcripción incompleta. El título del archivo no se usa como consulta. Después busca por separado documentos académicos y videos docentes. Prioriza editoriales, repositorios universitarios, artículos y libros, y filtra la relevancia con respecto a la transcripción. Si no logra extraer temas, informa del fallo y no vuelve a buscar solo por título.

No es una conexión directa a Google Scholar ni una garantía de revisión por pares. Los resultados proceden de la investigación de NotebookLM; los dominios académicos y los metadatos son indicios de procedencia, no una verificación del contenido completo. Se omiten enlaces genéricos y videos sin indicios suficientes de autoría académica. La interfaz indica si no se encuentran fuentes adecuadas o solo se obtiene una de las dos. Mantiene como máximo un texto y un video complementarios por clase.

El video generado usa estilo clásico y una instrucción de exposición académica, distinguiendo la clase de las aportaciones externas. Las opciones se comprobaron en la ayuda del CLI instalado y en la [documentación del proyecto notebooklm-py](https://github.com/teng-lin/notebooklm-py). Las consultas largas se pasan mediante un archivo temporal que se elimina al terminar para evitar el límite de comandos de Windows.

Las doce pruebas de regresión de `tests/test_academic_research.py` verifican consultas basadas en contenido, filtros, ausencia de sustitutos incorrectos, cancelación y limpieza de consultas temporales. Usan respuestas simuladas: no validan la calidad de una generación real ni publican en Drive o Classroom.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Se comprobó la sintaxis Python y, en una copia temporal sin cuentas, la carga de la página, JavaScript, estilos, configuración y listado de clases. Un TXT ficticio guardado por el receptor apareció en el listado del asistente. No se ejecutaron trabajos de generación ni publicaciones en servicios externos, ni se verificó el arranque del trabajador de tareas con trabajos reales. Se revisaron los archivos antes de publicarlos para excluir credenciales y datos privados.
