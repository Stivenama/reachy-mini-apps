"""Bounded RAM backlog with progress-aware drain and safe subprocess teardown."""
import base64
import collections
import contextlib
import json
import logging
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path
log=logging.getLogger(__name__)

class LocalASR:
    MAX_PENDING_BYTES=16000*2*120
    def __init__(self,callback):
        self.callback=callback
        self.ready,self.closed=threading.Event(),threading.Event()
        self.error='';self.waiters={};self.lock=threading.Lock()
        self.queue=queue.Queue()
        self.submitted_bytes=self.processed_bytes=self.renewals=0
        self.last_progress=time.monotonic()
        self.stderr_tail=collections.deque(maxlen=8)
        python=os.getenv('CLASSROOM_ASR_PYTHON',str(Path.home()/'apps/classroom_asr_venv/bin/python'))
        self.process=subprocess.Popen([python,'-u',str(Path(__file__).with_name('asr_worker.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1)
        self.writer=threading.Thread(target=self._write,daemon=True)
        self.reader=threading.Thread(target=self._read,daemon=True)
        self.diagnostics=threading.Thread(target=self._stderr,daemon=True)
        self.writer.start();self.reader.start();self.diagnostics.start()
    @property
    def lag_seconds(self):return max(0,self.submitted_bytes-self.processed_bytes)/32000
    def _fail(self,message):
        if not self.error:self.error=message
        self.ready.set()
        with self.lock:
            for event in self.waiters.values():event.set()
    def _stderr(self):
        for line in self.process.stderr:self.stderr_tail.append(line.strip()[:500])
    def _write(self):
        try:
            while not self.closed.is_set():
                try:msg=self.queue.get(timeout=.2)
                except queue.Empty:continue
                if msg['type']=='feed':
                    msg=dict(msg);msg['audio']=base64.b64encode(msg.pop('pcm')).decode()
                self.process.stdin.write(json.dumps(msg)+'\n');self.process.stdin.flush()
        except (BrokenPipeError,OSError,ValueError) as exc:
            if not self.closed.is_set():self._fail(f'El proceso de transcripción cerró su canal (código {self.process.poll()}); se conserva el texto recibido. {exc}')
    def _read(self):
        try:
            for line in self.process.stdout:
                msg=json.loads(line)
                if msg['type']=='ready':self.ready.set()
                elif msg['type']=='error':self._fail(msg['message'])
                elif msg['type']=='progress':
                    self.processed_bytes=msg['processed_bytes'];self.last_progress=time.monotonic()
                elif msg['type']=='renewed':
                    self.renewals+=1
                    log.info('CLASSROOM ASR_RENEWED count=%d lag_seconds=%.2f',self.renewals,self.lag_seconds)
                elif msg['type']=='flushed':
                    with self.lock:
                        waiter=self.waiters.get(msg['token'])
                        if waiter:waiter.set()
                elif msg['type'] in ('partial','final'):self.callback(msg)
        except Exception as exc:
            if not self.closed.is_set():self._fail(str(exc))
        finally:
            if not self.closed.is_set():self._fail(self.error or f'El transcriptor se detuvo (código {self.process.poll()})')
    def wait_ready(self):
        if not self.ready.wait(30):raise RuntimeError('El modelo español no pudo iniciar en 30 segundos')
        if self.error:raise RuntimeError(self.error)
    def feed(self,pcm,block):
        if self.error:raise RuntimeError(self.error)
        if self.closed.is_set():raise RuntimeError('El transcriptor está cerrado')
        if len(pcm)%2:raise ValueError('PCM incompleto')
        with self.lock:
            if self.submitted_bytes-self.processed_bytes+len(pcm)>self.MAX_PENDING_BYTES:
                raise RuntimeError('El transcriptor acumuló 120 segundos pendientes; se pausa la escucha y se termina de transcribir la cola. El audio posterior a esta pausa no se captura.')
            self.submitted_bytes+=len(pcm)
            for offset in range(0,len(pcm),6400):
                self.queue.put_nowait({'type':'feed','block':block,'pcm':pcm[offset:offset+6400]})
    def flush(self,wait=True):
        # An overflow stops intake, but MUST still allow accepted audio to drain.
        if self.error:raise RuntimeError(self.error)
        token,done=uuid.uuid4().hex,threading.Event()
        if wait:
            with self.lock:self.waiters[token]=done
        try:
            self.queue.put_nowait({'type':'flush','token':token})
            if wait:
                started=time.monotonic()
                while not done.wait(.2):
                    if self.error:raise RuntimeError(self.error)
                    now=time.monotonic()
                    if now-max(started,self.last_progress)>30 or now-started>300:
                        raise RuntimeError(f'El transcriptor dejó de avanzar; quedan {self.lag_seconds:.1f} segundos sin confirmar. Se conserva el texto recibido.')
            if self.error:raise RuntimeError(self.error)
        finally:
            with self.lock:self.waiters.pop(token,None)
    def close(self):
        if self.closed.is_set():return
        self.closed.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        self.writer.join(2);self.reader.join(2);self.diagnostics.join(2)
        # Closing buffered stdin after child exit may flush to a dead pipe.
        # That secondary error must never overwrite the actual capture failure.
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr):
            with contextlib.suppress(BrokenPipeError,OSError,ValueError):stream.close()
        if self.error:log.error('CLASSROOM ASR_CLOSED error=%s diagnostics=%s',self.error,list(self.stderr_tail))
