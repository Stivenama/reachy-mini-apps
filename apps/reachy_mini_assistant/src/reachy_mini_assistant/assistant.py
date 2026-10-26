"""Bucle conversacional del asistente (VAD + STT + LLM + TTS)."""

from __future__ import annotations

import asyncio
import logging
import time
import threading

import numpy as np
from fastapi import Request

from reachy_mini_assistant import config as config_mod
from reachy_mini_assistant import stt_llm, tools, tts
from reachy_mini_assistant.realtime_engine import RealtimeEngine

logger = logging.getLogger("reachy_mini_assistant")

SPEECH_RMS = 0.02
LISTEN_RMS = 0.035
SILENCE_S = 1.2
MAX_SEGMENT_SECONDS = 20.0
SAMPLE_RATE = 16000


def to_float32(frame: np.ndarray) -> np.ndarray:
    """Normaliza un frame de audio a float32 mono."""
    if frame is None or frame.size == 0:
        return np.array([], dtype=np.float32)
    a = np.asarray(frame)
    if a.dtype == np.int16:
        a = a.astype(np.float32) / 32768.0
    elif a.dtype != np.float32:
        a = a.astype(np.float32)
    if a.ndim == 2:
        a = a.mean(axis=1)
    return a.astype(np.float32)


def _rms(a: np.ndarray) -> float:
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(a))))


class Assistant:
    def __init__(self, cfg, on_state=None):
        self.cfg = cfg
        self.on_state = on_state or (lambda s: None)
        self.state = "idle"
        self.stream_active = False  # True mientras hay fragmentos en el pipeline de voz
        self.stop_requested = False
        self.speech_epoch = 0
        self.audio_lock = threading.RLock()
        self.cancel_pipeline = None
        self.engine = None  # RealtimeEngine (se asigna al arrancar)

    def set_state(self, state: str) -> None:
        self.state = state
        try:
            self.on_state(state)
        except Exception:
            pass

    def stop_speech(self, robot):
        with self.audio_lock:
            self.speech_epoch += 1
            if self.engine: self.engine.output_stopped = True
            robot.media.stop_playing()
            robot.media.start_playing()
            self.stream_active = False
            self.set_state('idle')
        if self.cancel_pipeline: self.cancel_pipeline()

    def speak(self, robot, text: str) -> None:
        text = (text or '').strip()
        if not text: return
        epoch = self.speech_epoch
        async def play():
            deadline=time.monotonic()
            async for audio in tts.stream_audio(self.cfg,text):
                if epoch != self.speech_epoch: return
                await asyncio.sleep(max(0,deadline-time.monotonic()-.15))
                with self.audio_lock:
                    if epoch != self.speech_epoch: return
                    self.set_state('speaking')
                    robot.media.push_audio_sample(audio)
                deadline=max(time.monotonic(),deadline)+len(audio)/16000
            while epoch == self.speech_epoch and time.monotonic()<deadline+.15:
                await asyncio.sleep(.02)
        try:
            self.set_state('speaking')
            asyncio.run(play())
        except Exception as e:
            logger.warning('TTS falló: %s',type(e).__name__)
        finally:
            if epoch == self.speech_epoch: self.set_state('idle')

    def handle_text(self, robot, text: str) -> None:
        if not text:
            return
        self.set_state("thinking")
        try:
            messages = [
                {"role": "system", "content": self.cfg.personality},
                {"role": "user", "content": text},
            ]
            resp = stt_llm.llm_chat(self.cfg, messages, tools.TOOLS)
            if resp.get("tool_name"):
                result = tools.execute(resp["tool_name"], resp.get("tool_args") or {})
                if result.get("stop_assistant"):
                    self.stop_requested = True
                    self.set_state("idle")
                    return
                self.speak(robot, result.get("result_text", ""))
            else:
                self.speak(robot, resp.get("text", ""))
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM falló: %s", e)
            self.speak(robot, "Hubo un problema procesando lo que dijiste.")
        finally:
            if self.state != "idle":
                self.set_state("idle")


def run_assistant(robot, stop_event, settings_app=None, logger=logger) -> None:
    """Arranca el asistente usando el motor del Hub (realtime) + voz ElevenLabs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s - %(levelname)s - %(message)s",
        force=True,
    )
    asyncio.run(_main(robot, stop_event, settings_app, logger))


async def _main(robot, stop_event, settings_app=None, logger=logger) -> None:
    cfg = config_mod.load_config()
    assistant = Assistant(cfg)

    if settings_app is not None:
        _register_routes(settings_app, assistant, robot)

    # Activar media (mic + parlante) y el gesto de wobbling (movimiento en reposo).
    try:
        robot.media.start_recording()
        robot.media.start_playing()
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo iniciar media: %s", e)
    try:
        robot.enable_wobbling()
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo habilitar wobbling: %s", e)

    engine = None
    engine_task = None
    mic_task = None
    synth_task = None
    speech_task = None
    watchdog_task = None
    text_q: asyncio.Queue = asyncio.Queue()   # texto -> sintetizador
    audio_q: asyncio.Queue = asyncio.Queue(maxsize=20)  # audio listo -> parlante
    try:
        hardware_id = ""
        try:
            status = robot.client.get_status(wait=False)
            hardware_id = getattr(status, "hardware_id", "") or ""
        except Exception:
            hardware_id = ""

        _TURN_END = "\x00TURN_END"

        def _engine_state(state: str) -> None:
            # El fin de turno se encola SIEMPRE (aunque esté "speaking"):
            # si no, el worker de reproducción espera para nunca y el estado
            # queda atascado en "Hablando" (sordo + widget mintiendo).
            if state == "idle":
                text_q.put_nowait((assistant.speech_epoch, _TURN_END))
            if assistant.state == "speaking":
                return
            assistant.set_state(state)

        async def _chunk_producer(text: str) -> None:
            assistant.stream_active = True
            await text_q.put((assistant.speech_epoch, text))

        loop = asyncio.get_running_loop()

        async def _speak_ack(text: str) -> None:
            """Aviso hablado inmediato (en el bucle de eventos)."""
            await asyncio.to_thread(assistant.speak, robot, text)

        def _notify(text: str) -> None:
            """Narrar desde hilos de fondo (hilo-seguro)."""
            try:
                loop.call_soon_threadsafe(text_q.put_nowait, (assistant.speech_epoch, text))
            except RuntimeError:
                pass

        async def _synth_worker() -> None:
            import httpx
            async with httpx.AsyncClient(timeout=45) as client:
                while True:
                    item=await text_q.get()
                    if item is None: return
                    epoch,chunk=item
                    if epoch != assistant.speech_epoch: continue
                    if chunk == _TURN_END:
                        await audio_q.put((epoch,_TURN_END)); continue
                    try:
                        async for audio in tts.stream_audio(cfg,chunk,client):
                            if epoch != assistant.speech_epoch: break
                            await audio_q.put((epoch,audio))
                    except Exception as e:
                        logger.warning('TTS de fragmento falló: %s',type(e).__name__)

        async def _play_worker() -> None:
            deadline=time.monotonic()
            while True:
                epoch,audio=await audio_q.get()
                if epoch != assistant.speech_epoch:
                    deadline=time.monotonic(); continue
                if isinstance(audio,str) and audio == _TURN_END:
                    await asyncio.sleep(max(0,deadline-time.monotonic())+.1)
                    if epoch == assistant.speech_epoch:
                        assistant.stream_active=False
                        assistant.set_state('idle')
                    continue
                await asyncio.sleep(max(0,deadline-time.monotonic()-.15))
                with assistant.audio_lock:
                    if epoch != assistant.speech_epoch:
                        deadline=time.monotonic(); continue
                    assistant.set_state('speaking')
                    robot.media.push_audio_sample(audio)
                deadline=max(time.monotonic(),deadline)+len(audio)/16000

        cancel_lock=asyncio.Lock()

        async def _cancel_pipeline():
            nonlocal synth_task
            async with cancel_lock:
                if synth_task:
                    synth_task.cancel()
                    await asyncio.gather(synth_task,return_exceptions=True)
                for queue in (text_q,audio_q):
                    while not queue.empty(): queue.get_nowait()
                synth_task=asyncio.create_task(_synth_worker(),name='tts-worker')
                if engine: await engine.cancel_response()

        assistant.cancel_pipeline=lambda: asyncio.run_coroutine_threadsafe(_cancel_pipeline(),loop)

        engine = RealtimeEngine(
            session_url=cfg.hf_session_url,
            instructions=cfg.personality,
            language=cfg.language,
            hardware_id=hardware_id,
            on_text_chunk=_chunk_producer,
            on_user_text=lambda text: assistant.set_state("listening"),
            on_state=_engine_state,
            on_ack=_speak_ack,
            on_notify=_notify,
        )
        assistant.engine = engine
        engine_task = asyncio.create_task(
            _engine_runner(engine, assistant, stop_event), name="hub-engine"
        )
        mic_task = asyncio.create_task(
            _mic_loop(engine, robot, assistant, stop_event), name="mic-loop"
        )
        synth_task = asyncio.create_task(_synth_worker(), name="tts-worker")
        speech_task = asyncio.create_task(_play_worker(), name="play-worker")
        watchdog_task = asyncio.create_task(
            _state_watchdog(assistant, text_q, audio_q, _TURN_END), name="state-watchdog"
        )

        if cfg.greet_on_start:
            await asyncio.sleep(2.5)  # dejar que el audio y la conexión estén listos
            await asyncio.to_thread(assistant.speak, robot, cfg.greeting)

        # Esperar hasta que nos detengan o el motor se caiga.
        done, _ = await asyncio.wait(
            [engine_task, mic_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                logger.warning("Tarea finalizó: %s", e)
        # Esperar a que llegue la orden de detener.
        while not stop_event.is_set() and not assistant.stop_requested:
            await asyncio.sleep(0.5)
    finally:
        assistant.cancel_pipeline = None
        assistant.speech_epoch += 1
        for task in (engine_task, mic_task, synth_task, speech_task, watchdog_task):
            if task is not None:
                task.cancel()
        if engine_task or mic_task or synth_task or speech_task or watchdog_task:
            await asyncio.sleep(0.3)
        try:
            robot.disable_wobbling()
        except Exception:
            pass
        try:
            robot.media.close()
        except Exception:
            pass
        logger.info("Asistente detenido.")


async def _speak_async(assistant, robot, text: str) -> None:
    await asyncio.to_thread(assistant.speak, robot, text)


async def _engine_runner(engine, assistant, stop_event) -> None:
    """Mantiene conectado el motor del Hub; reconecta si la sesión se cae."""
    while not stop_event.is_set() and not assistant.stop_requested:
        try:
            await engine.run()
        except Exception as e:  # noqa: BLE001
            logger.warning("Motor del Hub terminó con error: %s", e)
        if stop_event.is_set() or assistant.stop_requested:
            break
        logger.warning("Motor del Hub desconectado; reintentando en 5 s")
        await asyncio.sleep(5)


async def _state_watchdog(assistant, text_q, audio_q, turn_end: str) -> None:
    """Si el estado queda atascado en 'speaking' >60 s, lo fuerza a idle."""
    stuck_since: float | None = None
    while True:
        await asyncio.sleep(5)
        if assistant.state == "speaking":
            now = time.monotonic()
            if stuck_since is None:
                stuck_since = now
            elif now - stuck_since > 60:
                logger.warning("Watchdog: estado 'speaking' atascado >60s; limpiando y reactivando micro")
                try:
                    while not text_q.empty():
                        text_q.get_nowait()
                    while not audio_q.empty():
                        audio_q.get_nowait()
                    audio_q.put_nowait((assistant.speech_epoch, turn_end))
                except Exception:
                    pass
                assistant.stream_active = False
                assistant.set_state("idle")
                stuck_since = None
        else:
            stuck_since = None


async def _mic_loop(engine, robot, assistant, stop_event) -> None:
    """Envía el audio del micrófono del robot al motor del Hub (16 kHz mono int16)."""
    speaking_prev = False
    last_speaking_end = 0.0
    last_local_speech = 0.0
    while not stop_event.is_set() and not assistant.stop_requested:
        now = time.monotonic()
        # Always consume the recorder, including during speech/cooldown. Otherwise
        # queued speaker echo and stale user audio become the next conversation.
        try:
            frame = robot.media.get_audio_sample()
        except Exception:
            frame = None
        has_audio = frame is not None and getattr(frame, "size", 0) > 0
        # Mientras el asistente habla (o tiene fragmentos en cola) no se envía audio.
        if assistant.state == "speaking" or assistant.stream_active:
            speaking_prev = True
            await asyncio.sleep(0 if has_audio else 0.005)
            continue
        if speaking_prev:
            speaking_prev = False
            last_speaking_end = now
        # Cooldown tras hablar: con bafle externo el eco tarda en morir;
        # esperar más evita que Reachy se escuche a sí mismo y se interrumpa.
        if now - last_speaking_end < 2.5:
            await asyncio.sleep(0 if has_audio else 0.005)
            continue
        if has_audio:
            a = to_float32(frame)
            rms = _rms(a)
            # Detección local: "Escuchando" cuando hay voz, y "En espera" si hay silencio.
            if rms > LISTEN_RMS and assistant.state != "speaking":
                assistant.set_state("listening")
                last_local_speech = now
            elif assistant.state == "listening" and (now - last_local_speech) > 1.5:
                assistant.set_state("idle")
            pcm = (np.clip(a, -1, 1) * 32767).astype(np.int16)
            await engine.send_audio(pcm.tobytes())
        await asyncio.sleep(0 if has_audio else 0.005)


def _segment_seconds(buf: list[np.ndarray], rate: int) -> float:
    total = sum(a.size for a in buf)
    return total / max(rate, 1)


def _register_routes(settings_app, assistant, robot) -> None:
    from fastapi.responses import JSONResponse

    @settings_app.get("/state")
    def get_state() -> JSONResponse:
        eng = getattr(assistant, "engine", None)
        return JSONResponse(
            {
                "state": assistant.state,
                "ready": bool(eng and eng.connected.is_set() and assistant.state == "idle" and not assistant.stream_active),
                "user_text": eng.user_text if eng else "",
                "assistant_text": eng.assistant_text if eng else "",
                "config": config_mod.to_dict(assistant.cfg),
            }
        )

    @settings_app.post("/stop-speech")
    async def stop_speech() -> JSONResponse:
        await asyncio.to_thread(assistant.stop_speech, robot)
        return JSONResponse({"ok": True})

    @settings_app.post("/speak")
    async def speak(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = str(body.get("text") or "").strip()
        if not text:
            text = assistant.cfg.greeting
        await asyncio.to_thread(assistant.speak, robot, text)
        return JSONResponse({"ok": True, "text": text})

    @settings_app.post("/greet")
    async def greet() -> JSONResponse:
        await asyncio.to_thread(assistant.speak, robot, assistant.cfg.greeting)
        return JSONResponse({"ok": True, "text": assistant.cfg.greeting})

    @settings_app.post("/settings")
    async def set_settings(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        allowed = {
            "personality", "tts_provider", "piper_voice", "azure_voice",
            "azure_key", "azure_region", "elevenlabs_api_key", "elevenlabs_voice_id",
            "elevenlabs_model", "hf_session_url", "groq_api_key", "groq_stt_model",
            "groq_llm_model", "language", "greet_on_start", "greeting", "volume",
        }
        for key, value in body.items():
            if key in allowed and value is not None:
                setattr(assistant.cfg, key, value)
        config_mod.save_config(assistant.cfg)
        return JSONResponse({"ok": True, "config": config_mod.to_dict(assistant.cfg)})
