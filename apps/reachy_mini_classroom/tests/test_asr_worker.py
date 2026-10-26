import base64,importlib.util,io,json,pathlib,sys,types,unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
class WorkerTests(unittest.TestCase):
    def run_worker(self,seconds=25,lag=0,blocks=False):
        decoders=[]
        class Decoder:
            def __init__(self,*args):self.bytes=0;decoders.append(self)
            def AcceptWaveform(self,pcm):self.bytes+=len(pcm);return False
            def FinalResult(self):return json.dumps({'text':str(self.bytes)})
            def PartialResult(self):return '{}'
        spec=importlib.util.spec_from_file_location('worker',ROOT/'src/reachy_mini_classroom/asr_worker.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        messages=[dict(type='feed',block=2 if blocks and n>=10 else 1,lag_seconds=lag,audio=base64.b64encode(bytes(3200)).decode()) for n in range(seconds*10)]
        messages.append(dict(type='flush',token='done'))
        output=io.StringIO()
        fake=types.SimpleNamespace(Model=lambda _:None,KaldiRecognizer=Decoder,SetLogLevel=lambda _:None)
        with patch.dict(sys.modules,{'vosk':fake}),patch('sys.stdin',io.StringIO('\n'.join(map(json.dumps,messages)))),patch('sys.stdout',output):module.main()
        events=[json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(sum(d.bytes for d in decoders),seconds*32000)
        self.assertEqual([e for e in events if e['type']=='progress'][-1]['processed_bytes'],seconds*32000)
        self.assertEqual(events[-1],dict(type='flushed',token='done'))
        return events

    def test_normal_speech_not_forced_into_short_segments(self):
        events=self.run_worker()
        self.assertEqual(len([e for e in events if e['type']=='final']),1)

    def test_overload_keeps_recognition_context_and_processes_every_byte(self):
        events=self.run_worker(lag=10)
        finals=[e for e in events if e['type']=='final']
        self.assertEqual([e['seconds'] for e in finals],[25])
        self.assertEqual(sum(int(e['text']) for e in finals),25*32000)

    def test_exact_packet_block_change_keeps_old_text_in_old_block(self):
        events=self.run_worker(seconds=2,blocks=True)
        finals=[e for e in events if e['type']=='final']
        self.assertEqual([e['block'] for e in finals],[1,2])
        self.assertEqual([e['seconds'] for e in finals],[1,1])

if __name__=='__main__':unittest.main(verbosity=2)
