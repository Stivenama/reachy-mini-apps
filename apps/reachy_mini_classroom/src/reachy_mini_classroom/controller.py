"""Live Spanish text, Hub motion, and interruptible speech. No audio storage."""
import logging
import threading
import time
from .capture import BlockClock
from .local_asr import LocalASR

log = logging.getLogger(__name__)

class NoMotion:
    def listening(self, active): pass
    def speaking(self, active): pass
    def close(self): pass

class Controller:
    def __init__(self, store, backend, media, stop_event=None, motion=None, asr_factory=LocalASR):
        self.store, self.backend, self.media = store, backend, media
        self.stop_event = stop_event or threading.Event()
        self.motion, self.asr_factory = motion or NoMotion(), asr_factory
        self.lock, self.play_lock = threading.RLock(), threading.RLock()
        self.capture_done = threading.Event(); self.capture_done.set()
        self.capture_stop, self.response_stop = threading.Event(), threading.Event()
        self.capture_thread = self.response_thread = None
        self.pause_thread=None
        self.asr = None
        self.sid = self.block = None
        self.clock = BlockClock()
        self.phase, self.error = 'idle', ''
        self.live_text, self.live_final = '', False
        self.progress_note = ''
        self.piece_ids = {}
        self.playing = False
        self.play_deadline = 0
        self.store.recover()
        if hasattr(self.backend,'audit_guard'):
            self.backend.audit_guard=lambda: self.phase=='responding' and self.capture_done.is_set()

    def _state(self, phase, error=''):
        self.phase, self.error = phase, error
        if self.sid:
            self.store.execute('UPDATE sessions SET status=?,error=? WHERE id=?',(phase,error,self.sid))

    def status(self):
        with self.lock:
            return {'phase':self.phase,'session':self.sid,'error':self.error,
                    'elapsed':self.clock.elapsed,'level':self.clock.level,'block_minutes':20,
                    'live_text':self.live_text,'live_final':self.live_final,'language':'es',
                    'progress_note':self.progress_note,
                    'asr_ready':bool(self.phase=='listening' and self.asr and self.asr.ready.is_set() and not self.asr.error),
                    'asr_lag_seconds':getattr(self.asr,'lag_seconds',0),
                    'audio_saved':False,'remote_session_requests':getattr(self.backend,'session_requests',0),
                    'sessions':self.store.sessions()}

    def _busy(self):
        return self.phase in ('starting','listening','finishing','responding') or (
            self.response_thread is not None and self.response_thread.is_alive())

    def start(self, title='Clase', sid=None):
        with self.lock:
            if self._busy() or not self.capture_done.is_set(): raise ValueError('Hay una operación en curso')
            if sid: self.store.detail(sid)
            self.sid = sid or self.store.new_session(title)
            self.clock, self.block = BlockClock(), None
            self.piece_ids = {}
            self.live_text, self.live_final = '', False
            self.progress_note = ''
            self.capture_stop.clear(); self.capture_done.clear()
            self.store.execute("UPDATE sessions SET summary='' WHERE id=?",(self.sid,))
            self._state('starting')
            log.info('CLASSROOM START_CLASS local_asr=es')
            self.capture_thread = threading.Thread(target=self._capture,daemon=True,name='class-live-capture')
            self.capture_thread.start()
            return self.sid

    def _on_transcript(self, msg):
        if msg.get('block') is None: return
        with self.lock:
            key = (msg['block'],msg['turn'])
            text = msg.get('text','').strip()
            pid = self.piece_ids.get(key)
            if pid is None and (text or msg['type']=='final'):
                pid = self.store.new_piece(msg['block'])
                self.piece_ids[key] = pid
            if pid is not None:
                # Partial results are snapshots: UPDATE the same row, never concatenate.
                self.store.execute('UPDATE pieces SET text=?,seconds=?,status=? WHERE id=?',
                    (text,msg.get('seconds',0),'done' if msg['type']=='final' else 'draft',pid))
            if text:
                self.live_text = text
                self.live_final = msg['type']=='final'
                if self.live_final: log.info('CLASSROOM TRANSCRIPT_CONFIRMED block=%s chars=%d',msg['block'],len(text))

    def _capture(self):
        import numpy as np
        last_audio, last_save, last_voice = time.monotonic(), 0, 0
        last_listening = None
        try:
            self.asr = self.asr_factory(self._on_transcript)
            self.asr.wait_ready()
            log.info('CLASSROOM VOSK_STARTED')
            if self.capture_stop.is_set(): return
            self.media.start_recording()
            if self.media.get_input_audio_samplerate()!=16000:
                raise RuntimeError('El micrófono debe entregar PCM de 16 kHz, como el Hub')
            with self.lock: self._state('listening')
            while not self.capture_stop.is_set() and not self.stop_event.is_set():
                if self.asr.error: raise RuntimeError(self.asr.error)
                frame = self.media.get_audio_sample()
                if frame is None:
                    if time.monotonic()-last_audio>10: raise RuntimeError('El micrófono dejó de entregar audio')
                    self.capture_stop.wait(.005); continue
                frame=np.asarray(frame)
                if not frame.size: continue
                last_audio=time.monotonic()
                if frame.ndim==2:
                    if frame.shape[0]<frame.shape[1]: frame=frame.T
                    frame=frame[:,0]
                pcm=((np.clip(frame,-1,1)*32767).astype('<i2') if np.issubdtype(frame.dtype,np.floating) else frame.astype('<i2')).tobytes()
                if self.block is None: self.block=self.store.new_block(self.sid)
                close_block=self.clock.feed(pcm)
                self.asr.feed(pcm,self.block)
                if self.clock.level>self.clock.threshold: last_voice=time.monotonic()
                listening=time.monotonic()-last_voice<.7
                if listening!=last_listening:
                    self.motion.listening(listening); last_listening=listening
                    if listening: log.info('CLASSROOM SPEECH_DETECTED')
                if time.monotonic()-last_save>1 or close_block:
                    self.store.execute('UPDATE blocks SET duration=? WHERE id=?',(self.clock.elapsed,self.block))
                    last_save=time.monotonic()
                if close_block:
                    # Flush is ordered between old/new block audio and does not stop the mic.
                    self.asr.flush(wait=False)
                    self.store.execute('UPDATE blocks SET closed=1 WHERE id=?',(self.block,))
                    log.info('CLASSROOM BLOCK_SAVED block=%s',self.block)
                    self.block=None
                    self.clock=BlockClock()
        except Exception as exc:
            log.exception('Live recognition failed')
            with self.lock: self._state('paused',str(exc) or type(exc).__name__)
        finally:
            try: self.media.stop_recording()
            except Exception: log.exception('Stop recording failed')
            try:
                if self.asr: self.asr.flush()
            except Exception as exc:
                with self.lock: self._state('paused',self.error or str(exc))
            finally:
                try:
                    if self.asr: self.asr.close()
                except Exception as exc:
                    log.exception('ASR close failed')
                    with self.lock: self._state('paused',self.error or str(exc))
                try:
                    if self.block is not None:
                        self.store.execute('UPDATE blocks SET closed=1,duration=? WHERE id=?',(self.clock.elapsed,self.block))
                    self.motion.listening(False)
                except Exception as exc:
                    log.exception('Capture cleanup failed')
                    with self.lock: self._state('paused',self.error or str(exc))
                finally:
                    self.clock.level=0
                    self.capture_done.set()

    def pause(self):
        with self.lock:
            if self.phase in ('finishing','responding'): raise ValueError('Usa Pausar voz para detener la respuesta')
            if self.phase in ('listening','starting'):
                self.capture_stop.set(); self._state('finishing')
                self.pause_thread=threading.Thread(target=self._finish_pause,daemon=True)
                self.pause_thread.start()

    def _finish_pause(self):
        self.capture_done.wait()
        with self.lock: self._state('paused',self.error)

    def respond(self, sid=None):
        with self.lock:
            if self.phase in ('finishing','responding') or (self.response_thread and self.response_thread.is_alive()):
                raise ValueError('La respuesta ya está en proceso')
            if self.phase in ('starting','listening') and sid and sid!=self.sid:
                raise ValueError('Primero pausa la clase activa')
            if sid: self.store.detail(sid); self.sid=sid
            if not self.sid: raise ValueError('Selecciona o inicia una clase')
            self.capture_stop.set(); self.response_stop.clear()
            self.progress_note='Confirmando el texto pendiente'
            self._state('finishing')
            self.response_thread=threading.Thread(target=self._respond,args=(self.sid,),daemon=True,name='class-response')
            self.response_thread.start()

    def pause_voice(self):
        # Lock gate and pipeline stop ensure no stale callback can speak afterwards.
        with self.play_lock:
            self.response_stop.set()
            if self.playing:
                self.media.stop_playing(); self.playing=False
            self.motion.speaking(False)

    def _play_audio(self, pcm):
        import numpy as np
        for offset in range(0,len(pcm),640):
            # Keep 300 ms queued so the mixer isn't starved at packet boundaries.
            self.response_stop.wait(max(0,self.play_deadline-time.monotonic()-.3))
            with self.play_lock:
                if self.response_stop.is_set() or self.stop_event.is_set(): return
                if not self.playing:
                    self.media.start_playing(); self.playing=True
                    self.motion.speaking(True)
                    self.play_deadline=time.monotonic()
                chunk=pcm[offset:offset+640]
                self.media.push_audio_sample(np.frombuffer(chunk,dtype='<i2').astype(np.float32)/32768)
                self.play_deadline=max(time.monotonic(),self.play_deadline)+len(chunk)/32000

    def _progress(self,text):
        # Rebinding only: this runs on the response event loop and must never wait on self.lock.
        self.progress_note=text

    def _respond(self,sid):
        try:
            while not self.capture_done.wait(.1):
                if self.response_stop.is_set() or self.stop_event.is_set(): return
            if self.response_stop.is_set() or self.stop_event.is_set(): return
            if self.error: raise RuntimeError(self.error)
            data=self.store.detail(sid)
            if any(p['status']!='done' for b in data['blocks'] for p in b['pieces']):
                raise RuntimeError('Hay texto sin confirmar. Revisa la clase antes de resumir.')
            with self.lock: self._state('responding')
            log.info('CLASSROOM RESPOND_BEGIN blocks=%d',len(data['blocks']))
            self.backend.respond(
                texts=[f"Bloque {b['number']}\n{b['text']}" for b in data['blocks'] if b['text'].strip()],
                summary=data['summary'],
                on_summary=lambda text: self.store.execute('UPDATE sessions SET summary=? WHERE id=?',(text,sid)),
                on_audio=self._play_audio,cancel=self.response_stop,on_progress=self._progress)
            # Drain queued audio before stopping the device, including sink latency.
            if self.playing:
                self.response_stop.wait(max(0,self.play_deadline-time.monotonic())+.15)
            if not self.response_stop.is_set():
                with self.lock: self._state('complete')
        except Exception as exc:
            if not self.response_stop.is_set():
                log.exception('Response failed')
                with self.lock: self._state('paused',str(exc) or type(exc).__name__)
        finally:
            self.progress_note=''
            with self.play_lock:
                cancelled=self.response_stop.is_set()
                self.response_stop.set()  # gate outstanding callbacks after failure
                if self.playing: self.media.stop_playing(); self.playing=False
                self.motion.speaking(False)
            if cancelled:
                with self.lock: self._state('paused')

    def shutdown(self):
        self.capture_stop.set(); self.stop_event.set(); self.pause_voice()
        if self.capture_thread: self.capture_thread.join(25)
        if self.pause_thread: self.pause_thread.join(5)
        if self.response_thread: self.response_thread.join(5)
        self.motion.close()
        if self.sid and self.phase!='complete': self._state('paused',self.error)
