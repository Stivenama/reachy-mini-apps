"""Install the isolated Spanish recognizer on the robot."""
import pathlib
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

root = pathlib.Path.home() / 'apps'
venv = root / 'classroom_asr_venv'
models = root / 'classroom_models'
model = models / 'vosk-model-small-es-0.42'
if not (venv / 'bin/python').exists():
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(venv)], check=True)
subprocess.run([str(venv / 'bin/python'), '-m', 'pip', 'install', 'vosk==0.3.45'], check=True)
if not model.exists():
    models.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        archive = pathlib.Path(temporary) / 'model.zip'
        urllib.request.urlretrieve('https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip', archive)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if not (models / member.filename).resolve().is_relative_to(models.resolve()):
                    raise ValueError('Unsafe model archive')
            bundle.extractall(models)
print('Spanish recognizer ready:', model)
