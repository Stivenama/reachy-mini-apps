import importlib.util,pathlib,threading,time,unittest
root=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('voice_stream',root.parents[3]/'apps/reachy_mini_hub/src/reachy_mini_hub/elevenlabs_stream.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Response:
    def __init__(self,client):self.client=client;self.closed=False
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def raise_for_status(self):pass
    def close(self):self.closed=True
    def iter_bytes(self,chunk_size):
        for data in (b'\x01',b'\x02'*1599,b'\x03'*1600):
            if self.client.block:self.client.block.wait(2)
            if self.closed:return
            yield data
class Client:
    def __init__(self,block=None):self.calls=[];self.block=block
    def stream(self,*args,**kw):self.calls.append((args,kw));return Response(self)

class StreamTests(unittest.TestCase):
    def turn(self,client=None):
        played=[];states=[];errors=[]
        t=m.SpeechTurn(client or Client(),{'elevenlabs_voice_id':'test','elevenlabs_api_key':'test','elevenlabs_model':'test'},played.append,states.append,errors.append)
        self.addCleanup(t.cancel)
        return t,played,states,errors
    def test_chunks_start_before_final_and_preserve_text(self):
        c=m.TextChunks();text='Esta es una frase de prueba. Después sigue una explicación extensa con más palabras para comprobar el orden completo.'
        chunks=[]
        for char in text:chunks.extend(c.feed('audio',char))
        self.assertTrue(chunks)
        chunks.extend(c.feed('audio',text,True))
        self.assertEqual(' '.join(chunks),' '.join(text.split()))
        self.assertEqual(c.feed('text',text,True),[])
    def test_final_only_provider_and_duplicate_done(self):
        c=m.TextChunks();self.assertEqual(c.feed('text','Respuesta corta.',True),['Respuesta corta.'])
        self.assertEqual(c.feed('text','Respuesta corta.',True),[])
    def test_pcm_starts_before_final_text_and_preserves_odd_network_boundaries(self):
        t,played,states,errors=self.turn()
        t.feed('audio','Esta es una oración suficientemente larga. ')
        until=time.monotonic()+2
        while not played and time.monotonic()<until:time.sleep(.01)
        self.assertTrue(played);self.assertFalse(t.ended)
        t.finish();self.assertTrue(t.done.wait(3))
        self.assertEqual(b''.join(played),b'\x01'+b'\x02'*1599+b'\x03'*1600)
        self.assertEqual(states,['speaking','ready']);self.assertEqual(errors,[])
    def test_cancel_while_http_waits_prevents_all_late_playback(self):
        gate=threading.Event();client=Client(gate)
        t,played,states,errors=self.turn(client)
        t.feed('text','Texto suficiente para empezar la síntesis.',True);t.finish()
        until=time.monotonic()+2
        while not client.calls and time.monotonic()<until:time.sleep(.01)
        start=time.monotonic();t.cancel()
        self.assertLess(time.monotonic()-start,.2)
        gate.set();self.assertTrue(t.done.wait(2));t.producer.join(2)
        self.assertEqual(played,[]);self.assertFalse(t.producer.is_alive())
    def test_request_keeps_selected_voice_model_and_streaming_pcm(self):
        client=Client();t,played,states,errors=self.turn(client)
        t.feed('text','Hola.',True);t.finish();self.assertTrue(t.done.wait(2))
        args,kw=client.calls[0]
        self.assertTrue(args[1].endswith('/test/stream'))
        self.assertEqual(kw['json']['model_id'],'test')
        self.assertEqual(kw['params'],{'output_format':'pcm_16000'})

if __name__=='__main__':unittest.main(verbosity=2)
