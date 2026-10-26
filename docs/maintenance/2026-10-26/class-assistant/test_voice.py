import asyncio
import importlib
import pathlib
import sys
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch, Mock, AsyncMock

ROOT=pathlib.Path(__file__).parent
for package in ('reachy_mini_assistant','reachy_mini_classroom'):
    source=ROOT.parents[3]/'apps'/package/'src'
    sys.path.insert(0,str(source))
    module=importlib.import_module(package)
    module.__path__.insert(0,str(source/package))
from reachy_mini_assistant import tts, assistant, realtime_engine
from reachy_mini_classroom import backend

class Response:
    def __init__(self, packets): self.packets=packets;self.closed=False
    async def __aenter__(self): return self
    async def __aexit__(self,*args): self.closed=True
    def raise_for_status(self): pass
    async def aiter_bytes(self,size):
        for part in self.packets:
            yield part
            await asyncio.sleep(0)

class Client:
    def __init__(self,response): self.response=response;self.calls=[]
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    def stream(self,*args,**kwargs): self.calls.append((args,kwargs));return self.response

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_stop_drops_queued_audio_and_next_turn_works(self):
        import numpy as np
        stop=threading.Event();captured=[];written=[]
        cfg=NS(greet_on_start=False,hf_session_url='fake',personality='',language='es')
        class Engine:
            def __init__(self,**kwargs):
                self.__dict__.update(kwargs);self.connected=asyncio.Event();self.output_stopped=False
            async def run(self):
                self.connected.set()
                await self.on_text_chunk('Primera frase')
                await asyncio.sleep(30)
            async def cancel_response(self): self.output_stopped=True
        async def mic(*args):
            while not stop.is_set():await asyncio.sleep(.01)
        async def stream(*args):
            for _ in range(80):yield np.zeros(800,dtype=np.float32)
        robot=NS(media=Mock(),client=Mock(),enable_wobbling=Mock(),disable_wobbling=Mock())
        robot.media.push_audio_sample.side_effect=lambda audio:written.append(len(audio))
        with patch.object(assistant.config_mod,'load_config',return_value=cfg),patch.object(assistant,'RealtimeEngine',Engine),patch.object(assistant,'_mic_loop',side_effect=mic),patch.object(assistant,'_register_routes',side_effect=lambda app,a,r:captured.append(a)),patch.object(tts,'stream_audio',stream):
            task=asyncio.create_task(assistant._main(robot,stop,object()))
            try:
                for _ in range(100):
                    if written:break
                    await asyncio.sleep(.01)
                self.assertTrue(written)
                a=captured[0];a.stop_speech(robot);a.stop_speech(robot);count=len(written)
                await asyncio.sleep(.3)
                self.assertEqual(len(written),count)
                self.assertEqual(sum(t.get_name()=='tts-worker' and not t.done() for t in asyncio.all_tasks()),1)
                await a.engine.on_text_chunk('Segundo turno')
                for _ in range(100):
                    if len(written)>count:break
                    await asyncio.sleep(.01)
                self.assertGreater(len(written),count)
            finally:
                stop.set()
                await asyncio.wait_for(task,3)

    async def test_stream_preserves_bytes_and_selected_voice(self):
        import numpy as np
        raw=np.arange(1800,dtype='<i2').tobytes()
        response=Response([raw[:1201],raw[1201:]])
        client=Client(response)
        cfg=NS(tts_provider='elevenlabs',elevenlabs_api_key='fake',elevenlabs_voice_id='chosen',elevenlabs_model='chosen-model')
        with patch.object(tts,'_robotic_enabled',return_value=False):
            chunks=[x async for x in tts.stream_audio(cfg,'Prueba',client)]
        self.assertEqual(len(chunks),2)
        self.assertTrue(np.array_equal(np.concatenate(chunks),np.arange(1800)/32768))
        self.assertTrue(response.closed)
        self.assertIn('/chosen/stream',client.calls[0][0][1])
        self.assertEqual(client.calls[0][1]['json']['model_id'],'chosen-model')

    async def test_cancel_closes_stream(self):
        response=Response([b'\0'*1600]*4);client=Client(response)
        cfg=NS(tts_provider='elevenlabs',elevenlabs_api_key='fake',elevenlabs_voice_id='v',elevenlabs_model='m')
        with patch.object(tts,'_robotic_enabled',return_value=False):
            stream=tts.stream_audio(cfg,'Prueba',client)
            await anext(stream)
            await stream.aclose()
        self.assertTrue(response.closed)

    async def test_stop_ignores_late_output_and_resumes_next_speech(self):
        chunks=[]
        e=realtime_engine.RealtimeEngine('fake','',on_text_chunk=chunks.append)
        e.conn=NS(response=NS(cancel=AsyncMock()),input_audio_buffer=NS(clear=AsyncMock()))
        e.active_response='old'
        await e.cancel_response()
        await e._handle(e.conn,NS(type='response.output_audio_transcript.done',response_id='old',transcript='No hablar'))
        self.assertFalse(chunks)
        await e._handle(e.conn,NS(type='input_audio_buffer.speech_started'))
        await e._handle(e.conn,NS(type='response.output_audio_transcript.done',response_id='new',transcript='Nuevo turno'))
        self.assertEqual(chunks,['Nuevo turno'])

    async def test_stop_invalidates_playback(self):
        a=assistant.Assistant(NS());robot=NS(media=Mock())
        a.stream_active=True;a.engine=NS(output_stopped=False)
        a.stop_speech(robot)
        self.assertEqual(a.speech_epoch,1)
        self.assertFalse(a.stream_active)
        self.assertTrue(a.engine.output_stopped)
        robot.media.stop_playing.assert_called_once()

    async def test_chunk_keeps_word_space(self):
        chunk,rest=realtime_engine._take_chunk('Hola ')
        self.assertEqual(rest,'Hola ')
        self.assertEqual(realtime_engine._take_chunk(rest+'amigos, buenos días.')[0],'Hola amigos, buenos días.')

if __name__=='__main__': unittest.main()
