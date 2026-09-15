import asyncio
import pathlib
import struct
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'src'))
from reachy_mini_classroom.storage import Store
from reachy_mini_classroom.capture import BlockClock,RATE
from reachy_mini_classroom.controller import Controller
from reachy_mini_classroom.backend import HubBackend,ResponseCancelled

def pcm(value=5000,seconds=1): return struct.pack('<h',value)*int(RATE*seconds)

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name)
        self.sid=self.store.new_session('Clase')
    def tearDown(self): self.tmp.cleanup()

    def test_capture_failure_closes_block_and_keeps_primary_error(self):
        from unittest.mock import Mock,patch
        media=Mock();media.get_input_audio_samplerate.return_value=16000
        media.get_audio_sample.side_effect=RuntimeError('Original backlog error')
        asr=Mock();asr.error='';asr.flush.side_effect=RuntimeError('Drain error')
        asr.close.side_effect=BrokenPipeError('Secondary pipe error')
        c=Controller(self.store,object(),media,asr_factory=lambda callback:asr)
        c.sid=self.sid;c.block=self.store.new_block(self.sid)
        with patch('reachy_mini_classroom.controller.log.exception'):c._capture()
        self.assertEqual(c.error,'Original backlog error')
        self.assertTrue(c.capture_done.is_set())
        self.assertTrue(self.store.detail(self.sid)['blocks'][0]['closed'])

    def test_block_rollover_keeps_listening_without_backend(self):
        import numpy as np
        from unittest.mock import patch
        done=threading.Event()
        class Media:
            frames=0
            def start_recording(self): pass
            def stop_recording(self): pass
            def get_input_audio_samplerate(self): return 16000
            def get_audio_sample(self):
                if self.frames==6: done.set(); return None
                self.frames+=1
                return np.zeros(1600,dtype=np.int16)
        class ASR:
            error=''; ready=threading.Event()
            def __init__(self,callback): self.callback=callback; self.block=None; self.turn=0; self.ready.set()
            def wait_ready(self): pass
            def feed(self,pcm,block): self.block=block
            def flush(self,wait=True):
                self.callback({'type':'final','block':self.block,'turn':self.turn,'text':'tema','seconds':.1})
                self.turn+=1
            def close(self): pass
        backend=HubBackend()
        c=Controller(self.store,backend,Media(),asr_factory=ASR)
        with patch('reachy_mini_classroom.controller.BlockClock',lambda:BlockClock(.2,.1)):
            try:
                sid=c.start()
                self.assertTrue(done.wait(2))
                self.assertEqual(c.phase,'listening')
                self.assertEqual(backend.session_requests,0)
                self.assertEqual(len(self.store.detail(sid)['blocks']),3)
                c.pause(); self.assertTrue(c.capture_done.wait(2))
            finally: c.shutdown()

    def test_twenty_minutes_wait_for_next_silence(self):
        clock=BlockClock()
        for _ in range(1201): self.assertFalse(clock.feed(pcm()))
        self.assertFalse(clock.feed(pcm(0)))
        self.assertTrue(clock.feed(pcm(0)))
        self.assertEqual(clock.elapsed,1203)

    def test_silence_before_twenty_minutes_does_not_cut(self):
        c=BlockClock()
        self.assertFalse(c.feed(pcm(0,5)))
        self.assertEqual(c.elapsed,5)

    def test_partial_snapshots_replace_instead_of_duplicate(self):
        c=Controller(self.store,object(),object())
        b=self.store.new_block(self.sid)
        for text in ('el temple','el temple del acero','el temple del acero aumenta la dureza'):
            c._on_transcript({'type':'partial','block':b,'turn':0,'text':text,'seconds':3})
        c._on_transcript({'type':'final','block':b,'turn':0,'text':'el temple del acero aumenta la dureza','seconds':4})
        d=self.store.detail(self.sid)
        self.assertEqual(len(d['blocks'][0]['pieces']),1)
        self.assertEqual(d['blocks'][0]['text'],'el temple del acero aumenta la dureza')
        self.assertTrue(c.live_final)

    def test_repeated_separate_utterances_are_not_deleted(self):
        c=Controller(self.store,object(),object())
        b=self.store.new_block(self.sid)
        for turn in (0,1): c._on_transcript({'type':'final','block':b,'turn':turn,'text':'sí','seconds':1})
        self.assertEqual(self.store.detail(self.sid)['blocks'][0]['text'],'sí\nsí')

    def test_no_audio_files_and_recovery_preserves_text(self):
        b=self.store.new_block(self.sid); pid=self.store.new_piece(b)
        self.store.execute('UPDATE pieces SET text=? WHERE id=?',('última frase',pid))
        self.store.execute("UPDATE sessions SET status='listening' WHERE id=?",(self.sid,))
        self.store.recover()
        self.assertEqual(self.store.detail(self.sid)['blocks'][0]['text'],'última frase')
        self.assertEqual(self.store.detail(self.sid)['status'],'paused')
        self.assertFalse(list(pathlib.Path(self.tmp.name).rglob('*.pcm')))
        self.assertFalse(list(pathlib.Path(self.tmp.name).rglob('*.wav')))

    def test_cancel_pending_backend_task(self):
        cancel=threading.Event()
        async def pending(): await asyncio.sleep(10)
        threading.Timer(.1,cancel.set).start()
        started=time.monotonic()
        with self.assertRaises(ResponseCancelled): HubBackend.run(pending(),cancel)
        self.assertLess(time.monotonic()-started,1)

    def test_live_capture_final_text_before_speech_and_immediate_cancel(self):
        import numpy as np
        captured, speaking = threading.Event(),threading.Event()
        events=[]
        class Media:
            frames=0; plays=0; pushes=0
            def start_recording(self): events.append('record')
            def stop_recording(self): events.append('stop-record')
            def get_input_audio_samplerate(self): return 16000
            def get_audio_sample(self):
                if self.frames>=3: captured.set(); return None
                self.frames+=1; return np.full(16000,5000,dtype=np.int16)
            def start_playing(self): self.plays+=1; events.append('play')
            def stop_playing(self): events.append('stop-play')
            def push_audio_sample(self,audio): self.pushes+=1; speaking.set()
        class ASR:
            error=''; ready=threading.Event()
            def __init__(self,callback): self.callback=callback; self.block=None; self.ready.set()
            def wait_ready(self): pass
            def feed(self,audio,block):
                self.block=block
                self.callback({'type':'partial','turn':0,'block':block,'text':'temple','seconds':3})
            def flush(self,wait=True): self.callback({'type':'final','turn':0,'block':self.block,'text':'temple y templabilidad','seconds':3})
            def close(self): pass
        class Backend:
            def respond(self,texts,summary,on_summary,on_audio,cancel=None,on_progress=None):
                assert texts==['Bloque 1\ntemple y templabilidad']; events.append('summary'); on_summary('Resumen')
                for _ in range(100):
                    if cancel.is_set(): raise ResponseCancelled()
                    on_audio(bytes(3200))
        media=Media(); c=Controller(self.store,Backend(),media,asr_factory=ASR)
        try:
            sid=c.start()
            self.assertTrue(captured.wait(2)); self.assertEqual(media.plays,0)
            self.assertEqual(c.live_text,'temple')
            c.respond(sid)
            self.assertTrue(speaking.wait(2))
            with self.assertRaises(ValueError): c.respond(sid)
            c.pause_voice(); pushes=media.pushes
            c._play_audio(bytes(3200))
            self.assertEqual(media.pushes,pushes)
            c.response_thread.join(2)
            self.assertFalse(c.response_thread.is_alive())
            self.assertLess(events.index('stop-record'),events.index('play'))
            self.assertEqual(self.store.detail(sid)['summary'],'Resumen')
            self.assertFalse(list(pathlib.Path(self.tmp.name).rglob('*.wav')))
        finally: c.shutdown()

    def test_all_blocks_in_summary(self):
        seen=[]
        class B:
            def respond(self,texts,summary,on_summary,on_audio,cancel=None,on_progress=None):
                seen.extend(texts); on_summary('Resumen completo')
        c=Controller(self.store,B(),object()); c.sid=self.sid
        for text in ('Primer tema','Último tema'):
            b=self.store.new_block(self.sid)
            c._on_transcript({'type':'final','block':b,'turn':0,'text':text,'seconds':1})
            self.store.execute('UPDATE blocks SET closed=1 WHERE id=?',(b,))
        c._respond(self.sid)
        self.assertEqual(seen,['Bloque 1\nPrimer tema','Bloque 2\nÚltimo tema'])
        self.assertEqual(c.phase,'complete')

if __name__=='__main__': unittest.main()
