import asyncio
import contextlib
import pathlib
import sys
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'src'))
from reachy_mini_classroom.backend import HubBackend,ResponseCancelled

class SessionTests(unittest.TestCase):
    def test_long_class_summary_and_voice_use_one_connection(self):
        class Backend(HubBackend):
            opened=0
            closed=0
            seen=[]
            @contextlib.asynccontextmanager
            async def connection(self,text_only=False):
                self.opened+=1
                try: yield self
                finally: self.closed+=1
            async def _response(self,prompt,audio=False,on_audio=None,conn=None):
                assert conn is self and not audio
                self.seen.append((prompt,False))
                return 'Resumen en español'
            async def _speak(self,text,on_audio,conn,report=None):
                assert conn is self
                self.seen.append((text,True)); on_audio(b'\0\0')
        b=Backend(); summaries=[]; audio=[]
        # Long enough to still need several groups under the current budget.
        b.respond(['PRIMER_TEMA '+('a'*(3*HubBackend.group_chars()))+' ULTIMO_TEMA'],'',summaries.append,audio.append)
        self.assertEqual((b.opened,b.closed),(1,1))
        self.assertGreater(len(b.seen),2)
        self.assertTrue(any('PRIMER_TEMA' in p for p,_ in b.seen))
        self.assertTrue(any('ULTIMO_TEMA' in p for p,_ in b.seen))
        self.assertEqual(sum(a for _,a in b.seen),1)
        self.assertTrue(summaries and audio)

    def test_plain_text_expands_the_symbols_the_model_left_behind(self):
        plain=HubBackend.plain_text
        self.assertEqual(plain('unos 300.000 km/s'),'unos trescientos mil kilómetros por segundo')
        self.assertEqual(plain('asociada al número 137,'),'asociada al número ciento treinta y siete,')
        self.assertEqual(plain('cerca de −273 °C'),'cerca de menos doscientos setenta y tres grados celsius')
        self.assertEqual(plain('la constante 1/137 es'),'la constante uno partido por ciento treinta y siete es')
        self.assertEqual(plain('el cero absoluto, 0 K o'),'el cero absoluto, cero kelvin o')
        self.assertEqual(plain('un 5,5 % del total'),'un cinco coma cinco por ciento del total')
        # Idempotent: running it on already-plain text must not corrupt it.
        once=plain('son 300.000 km/s exactos')
        self.assertEqual(plain(once),once)

    def test_task_comes_after_the_transcript(self):
        class Backend(HubBackend):
            asked=''
            @contextlib.asynccontextmanager
            async def connection(self,text_only=False): yield self
            async def _response(self,prompt,audio=False,on_audio=None,conn=None):
                Backend.asked=prompt; return 'resumen breve'
        asyncio.run(Backend()._summarize(['MARCA_TRANSCRIPCION']))
        self.assertLess(Backend.asked.index('MARCA_TRANSCRIPCION'),Backend.asked.index('TAREA:'))
        self.assertIn('250 palabras o menos',Backend.asked)
        self.assertIn('Mantén las comas y los puntos con su uso normal',Backend.asked)

    def test_overlong_summary_is_retried_without_deleting_the_ending(self):
        import os
        from unittest.mock import patch
        class Backend(HubBackend):
            tries=0
            @contextlib.asynccontextmanager
            async def connection(self,text_only=False): yield self
            async def _response(self,prompt,audio=False,on_audio=None,conn=None):
                Backend.tries+=1
                return ' '.join(['Una frase de relleno bastante clara.']*40)  # 240 palabras
        with patch.dict(os.environ,{'CLASSROOM_SUMMARY_WORDS':'50'}):
            result=asyncio.run(Backend()._summarize(['clase']))
        self.assertEqual(Backend.tries,3)  # first attempt plus two shortening retries
        self.assertEqual(len(result.split()),240)
        self.assertTrue(result.endswith('.'))

    def test_groups_fill_the_budget_without_exceeding_it(self):
        budget=HubBackend.group_chars()
        groups=HubBackend._group(['x'*14000 for _ in range(6)],budget)
        self.assertTrue(all(len(g)<=budget for g in groups),[len(g) for g in groups])
        self.assertEqual(len(groups),3)

    def test_two_hour_class_costs_few_sequential_requests(self):
        class Backend(HubBackend):
            def __init__(self): super().__init__(); self.prompts=[]
            @contextlib.asynccontextmanager
            async def connection(self,text_only=False): yield self
            async def _response(self,prompt,audio=False,on_audio=None,conn=None):
                self.prompts.append((prompt,audio))
                return 'nota '*80
            async def _speak(self,text,on_audio,conn,report=None):
                self.prompts.append((text,True)); on_audio(b'\0\0')
        b=Backend(); steps=[]
        blocks=[f'Bloque {n}\n'+'palabra '*1750 for n in range(1,7)]  # ~84.000 caracteres
        b.respond(blocks,'',lambda text:None,lambda pcm:None,on_progress=steps.append)
        # Three groups, one final summary and one reading: the old 12.000 budget cost 17.
        self.assertEqual(len(b.prompts),5)
        self.assertEqual(sum(a for _,a in b.prompts),1)
        self.assertEqual(steps[0],'Conectando con el servicio de voz')
        self.assertIn('Resumiendo parte 1 de 3',steps)
        self.assertEqual(steps[-1],'Redactando el resumen final')

    def test_cached_summary_does_not_regenerate(self):
        class Backend(HubBackend):
            @contextlib.asynccontextmanager
            async def connection(self,text_only=False): yield self
            async def _summarize(self,*args): raise AssertionError('Must reuse saved summary')
            async def _response(self,*args,**kw): raise AssertionError('Must reuse saved summary')
            async def _speak(self,text,on_audio,conn,report=None):
                assert text=='guardado'; read.append(text)
        read=[]
        Backend().respond([], 'guardado',lambda text:None,lambda pcm:None)
        self.assertEqual(read,['guardado'])

    def test_listening_guard_prevents_http(self):
        b=HubBackend(); b.audit_guard=lambda:False
        with self.assertRaisesRegex(RuntimeError,'fuera del botón'):
            asyncio.run(b.endpoint())
        self.assertEqual(b.session_requests,0)

    def test_429_retry_after_does_not_loop_or_expose_url(self):
        import types
        fake_config=types.ModuleType('reachy_mini_hub.config')
        fake_config.config=types.SimpleNamespace(HF_TOKEN='')
        fake_config.get_hf_connection_selection=lambda:types.SimpleNamespace(mode='deployed',session_url='https://example.invalid/session')
        fake_config.get_hf_direct_ws_url=lambda:None
        fake_config.parse_hf_realtime_url=lambda u:u
        fake_config.HF_LOCAL_CONNECTION_MODE='local'
        fake_hf=types.ModuleType('huggingface_hub'); fake_hf.get_token=lambda:None
        class HTTP:
            posts=0
            def __init__(self,*a,**kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*a): pass
            async def post(self,*a,**kw):
                HTTP.posts+=1
                return types.SimpleNamespace(status_code=429,headers={'Retry-After':'300'})
        fake_http=types.ModuleType('httpx'); fake_http.AsyncClient=HTTP
        with patch.dict(sys.modules,{'httpx':fake_http,'huggingface_hub':fake_hf,'reachy_mini_hub.config':fake_config}):
            b=HubBackend()
            with self.assertRaisesRegex(RuntimeError,'429'): asyncio.run(b.endpoint())
            with self.assertRaisesRegex(RuntimeError,'Espera'): asyncio.run(b.endpoint())
            self.assertEqual(HTTP.posts,1)

if __name__=='__main__': unittest.main()
