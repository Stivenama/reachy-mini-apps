"""Fail closed on common secrets and private-data files; never print their values."""
import re
import json
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWED_ASSETS = json.loads((ROOT / 'scripts/reviewed_assets.json').read_text(encoding='utf-8'))
PATTERNS = {
    'token Google': re.compile(r'\bya29\.[A-Za-z0-9_-]+|\b1//[A-Za-z0-9_-]{20,}'),
    'token HF': re.compile(r'\bhf_[A-Za-z0-9]{20,}\b'),
    'token GitHub': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b'),
    'clave API': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b'),
    'clave privada': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'URL con credenciales': re.compile(r'https?://[^\s/:@]+:[^\s/@]+@'),
}
FORBIDDEN_PARTS = {'secrets', '.notebooklm', 'descargas', 'recordings', 'transcripciones', 'respaldos', 'sessions', '.venv', '.venvs', '__pycache__', 'external_content', 'user_personalities'}
FORBIDDEN_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.pcm', '.wav', '.mp3', '.mp4', '.pem', '.key', '.log', '.zip', '.gz'}

def inspect(path, data=None):
    rel = path.relative_to(ROOT)
    findings = []
    if any(p in FORBIDDEN_PARTS for p in rel.parts) or path.suffix in FORBIDDEN_SUFFIXES:
        findings.append('archivo privado o generado')
    if path.name.startswith('.env') and path.name != '.env.example':
        findings.append('configuración privada')
    if path.name in {'config.json', 'storage_state.json', 'client_secret.json', 'portal_config.json', 'profile_toolsets.json', 'startup_settings.json'}:
        findings.append('configuración de una instalación')
    data = path.read_bytes() if data is None else data
    if len(data) > 10 * 1024 * 1024:
        findings.append('archivo grande no revisado')
    if rel.as_posix() in REVIEWED_ASSETS:
        if hashlib.sha256(data).hexdigest() != REVIEWED_ASSETS[rel.as_posix()]:
            findings.append('recurso estático modificado: requiere revisión')
        return findings
    try:
        value = data.decode('utf-8')
    except UnicodeError:
        return findings + ['archivo binario no revisado']
    for label, pattern in PATTERNS.items():
        if pattern.search(value):
            findings.append(label)
    return findings

def main():
    blobs = {}
    if '--staged' in sys.argv:
        result = subprocess.run(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'], cwd=ROOT, capture_output=True, check=True)
        paths = [ROOT / x for x in result.stdout.decode().split('\0') if x]
        for path in paths:
            name = path.relative_to(ROOT).as_posix()
            blobs[path] = subprocess.run(['git', 'show', ':' + name], cwd=ROOT, capture_output=True, check=True).stdout
    elif (ROOT / '.git').exists():
        result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT, capture_output=True, check=True)
        paths = sorted({ROOT / x for x in result.stdout.decode().split('\0') if x and (ROOT / x).is_file()})
    else:
        paths = [p for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.relative_to(ROOT).parts]
    problems = [(str(p.relative_to(ROOT)), inspect(p, blobs.get(p))) for p in paths]
    problems = [(p, issues) for p, issues in problems if issues]
    for path, issues in problems:
        print(path + ': ' + ', '.join(issues))
    print(f'{len(paths)} archivos revisados; {len(problems)} archivos requieren revisión.')
    return 1 if problems else 0

if __name__ == '__main__':
    raise SystemExit(main())
