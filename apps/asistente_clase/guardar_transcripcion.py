"""Servicio local que guarda las transcripciones de "Clase continua" en una carpeta.

Escucha solo en 127.0.0.1 (este PC). El navegador, desde la app del robot,
le envia {session, title, content} y este servicio escribe <Titulo>.txt en la
carpeta indicada, manteniendo un indice para que los titulos repetidos usen
(2), (3)... y para actualizar el mismo archivo cuando la clase ya tiene resumen.
"""
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get('GUARDAR_HOST', '127.0.0.1')
PORT = int(os.environ.get('GUARDAR_PORT', '8765'))
BASE = Path(__file__).resolve().parent
OUTPUT = Path(os.environ.get('GUARDAR_DIR', str(BASE / 'transcripciones')))
INDEX = OUTPUT / '.indice.json'
LOG = BASE / 'guardar_transcripcion.log'
MAX_BODY = 30 * 1024 * 1024
ORIGIN_RE = re.compile(r'^http://[^/:]+:8093$')
_RESERVED = re.compile(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$')
_LOCK = threading.Lock()


def log(message):
    line = '%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), message)
    try:
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(line)
    except Exception:
        pass
    try:
        if sys.stdout is not None:
            sys.stdout.write(line)
            sys.stdout.flush()
    except Exception:
        pass


def sanitize(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', str(name or ''))
    name = re.sub(r'\s+', ' ', name).strip(' .')
    if _RESERVED.match(name):
        name = '_' + name
    return name[:120].strip(' .') or 'Clase'


def load_index():
    try:
        data = json.loads(INDEX.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_index(index):
    tmp = INDEX.with_name('.indice.tmp')
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(INDEX)


def allocate(session, title, index):
    if session in index:
        return index[session]
    base = sanitize(title)
    used = {str(value).lower() for value in index.values()}
    candidate = base + '.txt'
    number = 2
    while candidate.lower() in used or (OUTPUT / candidate).exists():
        candidate = '%s (%d).txt' % (base, number)
        number += 1
    index[session] = candidate
    return candidate


def store(payload):
    session = str(payload.get('session') or '').strip()
    title = payload.get('title')
    content = payload.get('content')
    if not session:
        raise ValueError('falta el identificador de la clase')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('el texto está vacío')
    with _LOCK:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        index = load_index()
        filename = allocate(session, title, index)
        target = OUTPUT / filename
        target.write_text(content, encoding='utf-8')
        save_index(index)
    return filename


class Handler(BaseHTTPRequestHandler):
    server_version = 'GuardarTranscripcion/1.0'

    def _origin(self):
        return self.headers.get('Origin', '')

    def _cors(self, extra=None):
        origin = self._origin()
        headers = {
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age': '600',
            'Vary': 'Origin',
        }
        if ORIGIN_RE.match(origin):
            headers['Access-Control-Allow-Origin'] = origin
        if self.headers.get('Access-Control-Request-Private-Network', '').lower() == 'true':
            headers['Access-Control-Allow-Private-Network'] = 'true'
        if extra:
            headers.update(extra)
        return headers

    def _send(self, status, payload, extra=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        for key, value in self._cors(extra).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        for key, value in self._cors().items():
            self.send_header(key, value)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        if self.path.split('?')[0] == '/health':
            self._send(200, {'ok': True, 'carpeta': str(OUTPUT)})
        else:
            self._send(404, {'ok': False, 'error': 'ruta desconocida'})

    def do_POST(self):
        if self.path.split('?')[0] != '/guardar':
            self._send(404, {'ok': False, 'error': 'ruta desconocida'})
            return
        origin = self._origin()
        if origin and not ORIGIN_RE.match(origin):
            self._send(403, {'ok': False, 'error': 'origen no permitido'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > MAX_BODY:
                raise ValueError('tamaño de petición incorrecto')
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            filename = store(payload)
        except Exception as error:  # noqa: BLE001
            log('ERROR %s' % error)
            self._send(400, {'ok': False, 'error': str(error)})
            return
        log('GUARDADO %s -> %s' % (payload.get('session'), filename))
        self._send(200, {'ok': True, 'archivo': filename, 'ruta': str(OUTPUT / filename)})

    def log_message(self, *args):
        pass


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as error:
        log('Ya hay un servicio escuchando en %s:%s (%s); no se inicia otro.' % (HOST, PORT, error))
        return
    log('Servicio activo en http://%s:%s -> %s' % (HOST, PORT, OUTPUT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log('Servicio detenido.')


if __name__ == '__main__':
    main()
