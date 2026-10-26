import asyncio,pathlib,sys,threading,time,types,unittest
from unittest.mock import Mock,AsyncMock,patch
source=pathlib.Path(__file__).resolve().parents[4]/'apps/reachy_mini_hub/src'
sys.path.insert(0,str(source))
import reachy_mini_hub
reachy_mini_hub.__path__.insert(0,str(source/'reachy_mini_hub'))
from reachy_mini_hub.console import LocalStream
from reachy_mini_hub.huggingface_realtime import HuggingFaceRealtimeHandler

class Controls(unittest.IsolatedAsyncioTestCase):
    def stream(self):
        s=LocalStream.__new__(LocalStream)
        s._listening=True;s._ready_announced=False;s._last_turn_state='listening'
        s._heard_speech=True;s._streamed_samples=16000;s._last_transcript='prueba'
        s._last_transcript_at=time.monotonic()-2;s._last_speech_at=time.monotonic()
        s._rpc=Mock();s._robot=Mock();s._speech_epoch=0;s._el_turn=None
        s._stop_event=threading.Event();s._audio_mode='robot';s._last_level_emit={}
        s.handler=Mock();s.handler.connection=object();s.handler._output_stopped=False
        s.handler._response_done_event=threading.Event();s.handler._response_done_event.set()
        s.handler.output_queue=asyncio.Queue();s.handler.cancel_response=AsyncMock()
        return s
    def test_ready_with_stable_transcript_despite_background_noise(self):
        s=self.stream();s._update_listening_ready()
        self.assertTrue(s._ready_announced);self.assertEqual(s._last_turn_state,'listening_ready')
        count=s._rpc.broadcast_threadsafe.call_count;s._update_listening_ready()
        self.assertEqual(s._rpc.broadcast_threadsafe.call_count,count)
    def test_ready_not_given_if_disconnected_or_response_still_busy(self):
        s=self.stream();s.handler.connection=None;s._update_listening_ready();self.assertFalse(s._ready_announced)
        s.handler.connection=object();s.handler._response_done_event.clear();s._update_listening_ready();self.assertFalse(s._ready_announced)
    def test_new_transcript_clears_ready_and_waits_again(self):
        s=self.stream();s._update_listening_ready();s._dispatch_transcript('user','otra frase',False)
        s._update_listening_ready();self.assertFalse(s._ready_announced)
    async def test_stop_cancels_external_native_and_pending_speaker_queue(self):
        s=self.stream();turn=Mock();s._el_turn=turn
        s.handler.output_queue.put_nowait((16000,b'audio'))
        result=await s.stop_speech()
        self.assertTrue(result['ok']);self.assertFalse(s._listening);self.assertTrue(s.handler._output_stopped)
        turn.cancel.assert_called_once();s.handler.cancel_response.assert_awaited_once()
        self.assertTrue(s.handler.output_queue.empty());s._robot.media.audio.clear_player.assert_called_once()
        self.assertEqual(s._last_turn_state,'ready')
    def test_external_voice_finishes_from_playback_not_model_done(self):
        s=self.stream();s._listening=False;s._last_turn_state='speaking'
        with patch('reachy_mini_hub.console._elevenlabs_active',return_value=True):s._dispatch_activity('response.done')
        self.assertEqual(s._last_turn_state,'speaking')
    def test_late_audio_of_cancelled_turn_is_suppressed_after_new_turn(self):
        h=HuggingFaceRealtimeHandler.__new__(HuggingFaceRealtimeHandler)
        h._output_stopped=False;h._cancelled_response_ids={'old'};h.push_to_talk_holding=False;h._suppressed_response_id=None
        self.assertTrue(h._suppressed_response_holds(types.SimpleNamespace(response_id='old')))
        self.assertFalse(h._suppressed_response_holds(types.SimpleNamespace(response_id='new')))
    async def test_cancel_discards_queued_responses_and_remembers_active_id(self):
        h=HuggingFaceRealtimeHandler.__new__(HuggingFaceRealtimeHandler)
        h._output_stopped=False;h._response_epoch=0;h._active_response_id='old';h._cancelled_response_ids=set()
        h._pending_responses=asyncio.Queue();h._pending_responses.put_nowait({})
        h._response_done_event=asyncio.Event();h.connection=Mock();h.connection.response.cancel=AsyncMock();h.deps=Mock()
        await h.cancel_response()
        self.assertTrue(h._output_stopped);self.assertEqual(h._response_epoch,1)
        self.assertTrue(h._pending_responses.empty());self.assertIn('old',h._cancelled_response_ids)
        h.connection.response.cancel.assert_awaited_once()
    async def test_native_tts_error_after_stop_does_not_restart_fallback(self):
        h=HuggingFaceRealtimeHandler.__new__(HuggingFaceRealtimeHandler)
        h.connection=object();h._output_stopped=False;h._response_epoch=0
        h.get_current_voice=lambda:'Aiden';h.client=Mock();h._say_verbatim=AsyncMock()
        async def fail_after_stop(**kwargs):
            h._output_stopped=True;h._response_epoch+=1
            raise RuntimeError('simulated network failure')
        h.client.audio.speech.create=fail_after_stop
        result=await h.read_text('prueba')
        self.assertEqual(result,'cancelled');h._say_verbatim.assert_not_awaited()

if __name__=='__main__':unittest.main(verbosity=2)
