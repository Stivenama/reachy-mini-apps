import importlib.util,io,json,pathlib,threading,types,unittest

root=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('asr',root/'src/reachy_mini_classroom/local_asr.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class BackpressureTests(unittest.TestCase):
    def test_stale_previews_do_not_block_progress_or_drop_final_text(self):
        a=module.LocalASR.__new__(module.LocalASR)
        a.submitted_bytes=32000*40;a.processed_bytes=0
        a.closed=threading.Event();a.closed.set()
        a.lock=threading.Lock();a.waiters={};a.renewals=0
        received=[];a.callback=received.append
        messages=[dict(type='partial',text='borrador antiguo'),dict(type='final',text='texto confirmado'),
                  dict(type='progress',processed_bytes=a.submitted_bytes),dict(type='partial',text='borrador actual')]
        a.process=types.SimpleNamespace(stdout=io.StringIO('\n'.join(map(json.dumps,messages))))
        a._read()
        self.assertEqual([m['text'] for m in received],['texto confirmado','borrador actual'])
        self.assertEqual(a.lag_seconds,0)

if __name__=='__main__':unittest.main(verbosity=2)
