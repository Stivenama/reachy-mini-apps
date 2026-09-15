import asyncio
import base64
import pathlib
import sys
import threading
import types
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'src'))
from reachy_mini_classroom.backend import HubBackend

class AudioTests(unittest.TestCase):
    def test_socket_drains_while_player_is_slow_and_preserves_pcm(self):
        drained=threading.Event()
        pcm=bytes(range(256))*125
        played=[]
        class Conn:
            def __init__(self): self.response=self
            async def create(self,**kw): pass
            def __aiter__(self): return self.events()
            async def events(self):
                for offset in range(0,len(pcm),3200):
                    yield types.SimpleNamespace(type='response.output_audio.delta',delta=base64.b64encode(pcm[offset:offset+3200]))
                    await asyncio.sleep(.001)
                drained.set()
                yield types.SimpleNamespace(type='response.output_audio_transcript.delta',delta='Resumen completo')
                yield types.SimpleNamespace(type='response.done',response=types.SimpleNamespace(status='completed'))
        def slow_player(chunk):
            # The old receiver waited for this callback before reading more.
            self.assertTrue(drained.wait(.5),'Playback blocked socket reception')
            played.append(chunk)
        result=asyncio.run(HubBackend()._response('texto',True,slow_player,Conn()))
        self.assertEqual(result,'Resumen completo')
        self.assertEqual(b''.join(played),pcm)

    def test_short_audio_tail_is_delivered(self):
        played=[]
        class Conn:
            def __init__(self): self.response=self
            async def create(self,**kw): pass
            def __aiter__(self): return self.events()
            async def events(self):
                yield types.SimpleNamespace(type='response.output_audio.delta',delta=base64.b64encode(b'\x01\x02'*321))
                yield types.SimpleNamespace(type='response.output_audio_transcript.delta',delta='sí')
                yield types.SimpleNamespace(type='response.done',response=types.SimpleNamespace(status='completed'))
        asyncio.run(HubBackend()._response('texto',True,played.append,Conn()))
        self.assertEqual(b''.join(played),b'\x01\x02'*321)

    def test_incomplete_response_reports_the_provider_reason(self):
        played=[]
        class Conn:
            def __init__(self): self.response=self
            async def create(self,**kw): pass
            def __aiter__(self): return self.events()
            async def events(self):
                yield types.SimpleNamespace(type='response.output_audio.delta',delta=base64.b64encode(bytes(32000)))
                yield types.SimpleNamespace(type='response.done',response=types.SimpleNamespace(
                    status='incomplete',status_details=types.SimpleNamespace(type='incomplete',reason='max_output_tokens')))
        with self.assertRaisesRegex(RuntimeError,'max_output_tokens.*1 s de audio'):
            asyncio.run(HubBackend()._response('texto',True,played.append,Conn()))

    def test_long_reading_is_split_and_every_part_is_played(self):
        played=[]
        class Conn:
            def __init__(self): self.response=self; self.prompts=[]
            async def create(self,**kw): self.prompts.append(kw['response']['input'][0]['content'][0]['text'])
            def __aiter__(self): return self.events()
            async def events(self):
                yield types.SimpleNamespace(type='response.output_audio.delta',delta=base64.b64encode(b'\x01\x02'*6000))
                yield types.SimpleNamespace(type='response.output_audio_transcript.delta',delta='x')
                yield types.SimpleNamespace(type='response.done',response=types.SimpleNamespace(status='completed'))
        text=' '.join(f'Frase número {n} con relleno suficiente para ocupar espacio.' for n in range(60))
        conn=Conn(); steps=[]
        asyncio.run(HubBackend()._speak(text,played.append,conn,steps.append))
        self.assertGreater(len(conn.prompts),1)
        # Nothing is dropped between parts: every byte of every response reaches the player.
        self.assertEqual(len(b''.join(played)),len(conn.prompts)*12000)
        self.assertEqual(steps[0],f'Leyendo parte 1 de {len(conn.prompts)}')
        self.assertTrue(all(len(p)<=HubBackend.speech_chars()+len(HubBackend.SPEECH_PROMPT) for p in conn.prompts))

    def test_speech_parts_cut_on_sentence_ends(self):
        text='Uno dos tres. Cuatro cinco seis! Siete ocho nueve? Diez once doce.'
        self.assertEqual(HubBackend._speech_parts(text,34),
                         ['Uno dos tres. Cuatro cinco seis!','Siete ocho nueve? Diez once doce.'])
        # A sentence longer than the budget still has to be broken, but never silently dropped.
        self.assertEqual(''.join(HubBackend._speech_parts('x'*500,200)),'x'*500)
        long_sentence=' '.join(['palabra']*100)+' Finalmente el cierre completo.'
        parts=HubBackend._speech_parts(long_sentence,200)
        self.assertEqual(' '.join(parts),long_sentence)
        self.assertTrue(parts[-1].endswith('Finalmente el cierre completo.'))

    def test_output_cap_is_configurable_and_omittable(self):
        import os
        from unittest.mock import patch
        self.assertEqual(HubBackend.max_output_tokens(),'inf')
        with patch.dict(os.environ,{'CLASSROOM_MAX_OUTPUT_TOKENS':'4096'}):
            self.assertEqual(HubBackend.max_output_tokens(),4096)
        with patch.dict(os.environ,{'CLASSROOM_MAX_OUTPUT_TOKENS':'default'}):
            self.assertIsNone(HubBackend.max_output_tokens())

if __name__=='__main__': unittest.main()
