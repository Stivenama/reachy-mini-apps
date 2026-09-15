import pathlib
import queue
import sys
import threading
import time
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'src'))
from reachy_mini_classroom.local_asr import LocalASR

class ASRTests(unittest.TestCase):
    def harness(self):
        a=LocalASR.__new__(LocalASR)
        a.error='';a.closed=threading.Event();a.ready=threading.Event()
        a.lock=threading.Lock();a.queue=queue.Queue();a.waiters={}
        a.submitted_bytes=a.processed_bytes=0;a.last_progress=time.monotonic()
        return a
    def test_full_audio_budget_rejects_whole_frame_and_can_flush(self):
        a=self.harness();a.submitted_bytes=a.MAX_PENDING_BYTES-2
        with self.assertRaisesRegex(RuntimeError,'120 segundos'):a.feed(bytes(4),1)
        self.assertTrue(a.queue.empty())
        self.assertEqual(a.submitted_bytes,a.MAX_PENDING_BYTES-2)
        a.flush(wait=False)
        self.assertEqual(a.queue.get()['type'],'flush')
        self.assertEqual(a.error,'')
    def test_close_dead_pipe_does_not_mask_original_error(self):
        a=self.harness();a.error='Original recognition failure';a.stderr_tail=[]
        a.process=Mock();a.process.poll.return_value=1
        for stream in (a.process.stdin,a.process.stdout,a.process.stderr):stream.close.side_effect=BrokenPipeError()
        a.writer=Mock();a.reader=Mock();a.diagnostics=Mock()
        a.close();a.close()
        self.assertEqual(a.error,'Original recognition failure')
        self.assertTrue(a.closed.is_set())
    def test_acknowledged_audio_frees_budget(self):
        a=self.harness();a.submitted_bytes=a.MAX_PENDING_BYTES
        a.processed_bytes=32000
        a.feed(bytes(32000),2)
        self.assertEqual(a.lag_seconds,120)
        self.assertEqual(sum(len(m['pcm']) for m in list(a.queue.queue)),32000)

if __name__=='__main__':unittest.main()
