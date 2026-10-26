"""Ordered, cancellable text/PCM streaming. No robot or credential side effects."""
import logging
import queue
import threading
import time

log = logging.getLogger(__name__)


class TextChunks:
    def __init__(self):
        self.channel = None
        self.buffer = ''
        self.emitted = False
        self.finished = False
        self.received = False

    def feed(self, channel, text, final=False):
        if not text and not final:
            return []
        if self.finished or (self.channel is not None and self.channel != channel):
            return []
        self.channel = channel
        if final:
            if not self.received:
                self.buffer = text or ''
            self.finished = True
        else:
            self.received = True
            self.buffer += text or ''
        chunks = []
        while self.buffer.strip():
            budget = 140 if self.emitted else 60
            cut = next((i+1 for i,c in enumerate(self.buffer[:budget])
                        if c in '.!?;:' and i >= 20), 0)
            if not cut and len(self.buffer) >= budget:
                cut = self.buffer.rfind(' ', 0, budget)
                if cut < 1: cut = budget
            if not cut and self.finished: cut = len(self.buffer)
            if not cut: break
            piece, self.buffer = self.buffer[:cut], self.buffer[cut:]
            if piece.strip(): chunks.append(piece.strip()); self.emitted = True
        return chunks


class SpeechTurn:
    """One synthesis producer + one paced player; cancellation gates every write."""
    def __init__(self, client, config, play, on_state, on_error):
        self.client, self.config = client, dict(config)
        self.play, self.on_state, self.on_error = play, on_state, on_error
        self.cancelled = threading.Event()
        self.lock = threading.RLock()
        self.text = queue.Queue(maxsize=256)
        self.audio = queue.Queue(maxsize=40)  # two seconds of 50 ms PCM
        self.chunks = TextChunks()
        self.started = time.monotonic()
        self.response = None
        self.ended = False
        self.char_count = 0
        self.done = threading.Event()
        self.producer = threading.Thread(target=self._synthesize, daemon=True)
        self.player = threading.Thread(target=self._play, daemon=True)
        self.producer.start(); self.player.start()

    def feed(self, channel, text, final=False):
        if self.cancelled.is_set() or self.ended: return
        for piece in self.chunks.feed(channel, text, final):
            self.char_count += len(piece)
            if self.char_count > 40000:
                self.cancel(); self.on_error('La respuesta de voz excedió el límite de longitud.'); return
            try: self.text.put_nowait(piece)
            except queue.Full:
                self.cancel(); self.on_error('La cola de voz está llena. Vuelve a intentarlo.'); return

    def finish(self):
        if self.ended or self.cancelled.is_set(): return
        self.feed(self.chunks.channel or 'text', '', True)
        self.ended = True
        try: self.text.put_nowait(None)
        except queue.Full:
            self.cancel(); self.on_error('La cola de voz está llena.')

    def cancel(self):
        with self.lock:
            self.cancelled.set()
        # Closing a stalled HTTP response must never block the control button.
        response = self.response
        if response is not None:
            def close():
                try: response.close()
                except Exception: pass
            threading.Thread(target=close, daemon=True).start()

    def _put_audio(self, data):
        while not self.cancelled.is_set():
            try: self.audio.put(data, timeout=.05); return
            except queue.Full: pass

    def _synthesize(self):
        previous = ''
        try:
            while not self.cancelled.is_set():
                try: text = self.text.get(timeout=.1)
                except queue.Empty: continue
                if text is None: break
                cfg = self.config
                body = {'text': text, 'model_id': cfg.get('elevenlabs_model') or 'eleven_flash_v2_5'}
                if previous: body['previous_text'] = previous[-1000:]
                with self.client.stream('POST',
                        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['elevenlabs_voice_id']}/stream",
                        params={'output_format': 'pcm_16000'},
                        headers={'xi-api-key': cfg['elevenlabs_api_key']}, json=body,
                        timeout=30) as response:
                    self.response = response
                    response.raise_for_status()
                    pending = b''
                    for chunk in response.iter_bytes(chunk_size=1600):
                        if self.cancelled.is_set(): return
                        pending += chunk
                        count = len(pending) // 2 * 2
                        if count: self._put_audio(pending[:count]); pending = pending[count:]
                    if pending: raise ValueError('PCM incompleto del proveedor de voz')
                self.response = None
                previous += ' ' + text
        except Exception as exc:
            if not self.cancelled.is_set():
                log.warning('ElevenLabs streaming failed (%s)', type(exc).__name__)
                self.on_error('No se pudo completar la voz de ElevenLabs. Comprueba la conexión o la configuración de voz.')
        finally:
            self.response = None
            self._put_audio(None)

    def _play(self):
        deadline = time.monotonic()
        first = True
        try:
            while not self.cancelled.is_set():
                try: pcm = self.audio.get(timeout=.1)
                except queue.Empty: continue
                if pcm is None:
                    self.cancelled.wait(max(0, deadline-time.monotonic())+.1)
                    break
                if self.cancelled.wait(max(0, deadline-time.monotonic()-.15)): break
                with self.lock:
                    if self.cancelled.is_set(): break
                    if first:
                        log.info('ElevenLabs first playback %.0f ms after response.created', (time.monotonic()-self.started)*1000)
                        self.on_state('speaking'); first = False
                    self.play(pcm)
                deadline = max(deadline, time.monotonic()) + len(pcm)/32000
        except Exception:
            self.cancel()
            self.on_error('No se pudo reproducir la voz.')
        finally:
            self.done.set()
            if not self.cancelled.is_set(): self.on_state('ready')
