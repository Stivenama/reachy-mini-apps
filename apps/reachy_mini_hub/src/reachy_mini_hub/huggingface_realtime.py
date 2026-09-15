import json
import time
import uuid
import base64
import random
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final, Tuple, Optional

import httpx
import numpy as np
from openai import AsyncOpenAI
from pydantic import Field, BaseModel
from numpy.typing import NDArray
from huggingface_hub import get_token
from typing_extensions import Literal, TypedDict
from openai.types.realtime import (
    AudioTranscriptionParam,
    RealtimeAudioConfigParam,
    RealtimeToolsConfigParam,
    RealtimeFunctionToolParam,
    RealtimeAudioConfigInputParam,
    RealtimeAudioConfigOutputParam,
    RealtimeSessionCreateRequestParam,
)
from websockets.exceptions import ConnectionClosedError
from openai.types.realtime.realtime_audio_input_turn_detection_param import ServerVad

from reachy_mini_hub.tools import core_tools
from reachy_mini_hub.config import (
    HF_LOCAL_CONNECTION_MODE,
    config,
    get_default_voice,
    set_custom_profile,
    get_available_voices,
    get_hf_direct_ws_url,
    parse_hf_realtime_url,
    get_hf_connection_selection,
    push_to_talk_enabled,
)
from reachy_mini_hub.prompts import (
    get_session_voice,
    get_session_instructions,
    get_session_greeting_prompt,
)
from reachy_mini_hub.streaming import AdditionalOutputs, audio_to_int16
from reachy_mini_hub.tools.core_tools import (
    ToolSpec,
    ToolDependencies,
    get_tool_specs,
)
from reachy_mini_hub.conversation_handler import AudioFrame, ConversationHandler
from reachy_mini_hub.tools.background_tool_manager import (
    ToolCallRoutine,
    ToolNotification,
    BackgroundToolManager,
)


if TYPE_CHECKING:
    from openai.resources.realtime.realtime import AsyncRealtimeConnection


logger = logging.getLogger(__name__)

_RESPONSE_DONE_TIMEOUT: Final[float] = 30.0
_RESPONSE_REJECTION_RETRY_DELAY: Final[float] = 0.5

# Reachy Hub mode: after every user turn the model replies with a spoken
# summary of the whole session (~1 minute) and ends with one question.
SUMMARY_MODE_INSTRUCTIONS = (
    "IMPORTANTE: después de cada turno del usuario responde de forma hablada "
    "resumiendo TODO lo que se ha hablado en la conversación actual, en un máximo "
    "de 250 palabras (aproximadamente 1 minuto hablando), y termina SIEMPRE con "
    "UNA sola pregunta relacionada con lo que escuchaste."
)


class InputTranscriptChunksByItem(BaseModel):
    """Current item_id and its accumulated deltas. Only one item at a time."""

    item_id: str | None = None
    deltas: list[str] = Field(default_factory=list)


def to_realtime_tools_config(tool_specs: list[ToolSpec]) -> RealtimeToolsConfigParam:
    """Convert app tool specs to the OpenAI-compatible realtime session shape."""
    realtime_tools: RealtimeToolsConfigParam = []
    for spec in tool_specs:
        realtime_tools.append(
            RealtimeFunctionToolParam(
                type="function",
                name=spec["name"],
                description=spec["description"],
                parameters=spec["parameters"],
            )
        )
    return realtime_tools


class HFNativeRateAudioPCM(TypedDict):
    """Hugging Face extension for native-rate PCM audio."""

    type: Literal["audio/pcm"]
    rate: None


def _native_rate_audio_pcm() -> HFNativeRateAudioPCM:
    """Return the Hugging Face native-rate PCM config."""
    return {"type": "audio/pcm", "rate": None}


def _wav_to_pcm16(wav: bytes) -> tuple[int, NDArray[np.int16] | None]:
    """Parse a WAV buffer into ``(sample_rate, mono int16 PCM)``."""
    if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        return 0, None
    rate = 0
    channels = 1
    pos = 12
    while pos + 8 <= len(wav):
        chunk_id = wav[pos : pos + 4]
        chunk_size = int.from_bytes(wav[pos + 4 : pos + 8], "little")
        data_start = pos + 8
        if chunk_id == b"fmt ":
            fmt = wav[data_start : data_start + chunk_size]
            if len(fmt) >= 16 and int.from_bytes(fmt[0:2], "little") == 1:
                channels = int.from_bytes(fmt[2:4], "little")
                rate = int.from_bytes(fmt[4:8], "little")
                bits = int.from_bytes(fmt[14:16], "little")
                if bits != 16 or channels == 0:
                    return 0, None
        elif chunk_id == b"data":
            raw = wav[data_start : data_start + chunk_size]
            pcm = np.frombuffer(raw, dtype=np.int16)
            if channels > 1:
                pcm = pcm[::channels]
            return rate or 16000, pcm.astype(np.int16)
        pos = data_start + chunk_size + (chunk_size & 1)
    return 0, None


def _resample_pcm16(pcm: NDArray[np.int16], src_rate: int, dst_rate: int) -> NDArray[np.int16]:
    """Linear resample of mono int16 PCM to ``dst_rate`` Hz."""
    if src_rate == dst_rate or pcm.size == 0:
        return pcm
    src = pcm.astype(np.float64)
    n_out = max(1, int(round(len(src) * dst_rate / src_rate)))
    x_old = np.linspace(0.0, 1.0, len(src), endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, src).astype(np.int16)


def _build_openai_compatible_client_from_realtime_url(
    realtime_url: str,
    bearer_token: str | None,
) -> tuple[AsyncOpenAI, dict[str, str]]:
    """Build an OpenAI-compatible realtime client from a direct websocket/base URL."""
    parsed = parse_hf_realtime_url(realtime_url)
    client = AsyncOpenAI(
        api_key=bearer_token or "DUMMY",
        base_url=parsed.base_url,
        websocket_base_url=parsed.websocket_base_url,
    )
    return client, parsed.connect_query


class HuggingFaceRealtimeHandler(ConversationHandler):
    """Realtime stream handler for the Hugging Face OpenAI-compatible endpoint."""

    SAMPLE_RATE = 16000

    def __init__(
        self,
        deps: ToolDependencies,
        instance_path: Optional[str] = None,
        startup_voice: Optional[str] = None,
    ):
        """Initialize the handler."""
        super().__init__()

        self.deps = deps

        self.client: AsyncOpenAI
        self.connection: "AsyncRealtimeConnection | None" = None
        self.output_queue: "asyncio.Queue[Tuple[int, NDArray[np.int16]] | AdditionalOutputs]" = asyncio.Queue()

        self.instance_path = instance_path
        self._voice_override: str | None = self._normalize_startup_voice(startup_voice)
        self._realtime_connect_query: dict[str, str] = {}

        # Debouncing for partial transcripts
        self.partial_transcript_task: asyncio.Task[None] | None = None
        self.partial_debounce_delay = 0.5  # seconds
        self.input_transcript_chunks_by_item = InputTranscriptChunksByItem()

        # Internal lifecycle flags
        self._connected_event: asyncio.Event = asyncio.Event()

        # Background tool manager
        self.tool_manager = BackgroundToolManager()

        # Response-in-progress guard: the Realtime API only allows one active
        # response per conversation at a time.  A dedicated worker task
        # (_response_sender_loop) dequeues and sends one request at a time
        self._pending_responses: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._response_done_event: asyncio.Event = asyncio.Event()
        self._response_done_event.set()
        self._response_started_or_rejected_event: asyncio.Event = asyncio.Event()
        self._last_response_rejected: bool = False
        self._turn_user_done_at: float | None = None
        self._turn_response_created_at: float | None = None
        self._turn_first_audio_at: float | None = None
        self._startup_greeting_sent = False
        self._in_flight_tool_calls: set[str] = set()
        self._tool_batch_needs_response = False
        # Push-to-talk: a response that started while the talk button was held
        # is suppressed (never played) and re-requested on button release.
        self._suppressed_response = False
        self._suppressed_response_id: str | None = None
        # Backend degradation: some hosted models reject function tools
        # (e.g. gpt-5.6-terra with reasoning_effort). Once detected, tools are
        # disabled for the session so the conversation keeps working.
        self._tools_disabled = False

    @staticmethod
    def _sanitize_tool_result_for_model(tool_name: str, tool_result: dict[str, Any]) -> dict[str, Any]:
        """Remove bulky transport-only fields before echoing tool output back to the model."""
        if tool_name == "camera" and "b64_im" in tool_result:
            sanitized = dict(tool_result)
            sanitized.pop("b64_im", None)
            sanitized["image_attached"] = True
            return sanitized
        return tool_result

    def _normalize_startup_voice(self, voice: str | None) -> str | None:
        """Return a valid persisted startup voice, or None."""
        return self._resolve_backend_voice(voice, source="persisted startup voice")

    async def _wait_for_response_done_before_tool_result(self) -> bool:
        """Return whether the function-call response finished before sending tool output."""
        if self._response_done_event.is_set():
            return True

        try:
            await asyncio.wait_for(
                self._response_done_event.wait(),
                timeout=_RESPONSE_DONE_TIMEOUT,
            )
            return True
        except asyncio.TimeoutError:
            return False

    def _resolve_backend_voice(
        self,
        voice: str | None,
        *,
        source: str,
        fallback: str | None = None,
    ) -> str | None:
        """Return a backend-supported voice, optionally falling back when unsupported."""
        available_voices = get_available_voices()
        voice_value = (voice or "").strip()
        if not voice_value:
            return fallback

        voice_by_lowercase = {candidate.lower(): candidate for candidate in available_voices}
        normalized_voice = voice_by_lowercase.get(voice_value.lower())
        if normalized_voice is not None:
            return normalized_voice

        if voice:
            logger.warning(
                "Ignoring unsupported %s %r; expected one of %s",
                source,
                voice,
                available_voices,
            )
        return fallback

    def _get_session_config(self, tool_specs: list[ToolSpec]) -> RealtimeSessionCreateRequestParam:
        """Return the Hugging Face OpenAI-compatible session config."""
        # Push-to-talk disables server VAD: the user commits audio with a
        # button press instead of the backend answering on silence.
        turn_detection: ServerVad | None
        if push_to_talk_enabled():
            turn_detection = None
        else:
            turn_detection = ServerVad(type="server_vad", interrupt_response=True)
        return RealtimeSessionCreateRequestParam(
            type="realtime",
            instructions=f"{get_session_instructions(self.instance_path)}\n\n{SUMMARY_MODE_INSTRUCTIONS}",
            audio=RealtimeAudioConfigParam(
                input=RealtimeAudioConfigInputParam(
                    # The OpenAI SDK type only includes 24 kHz PCM, but the HF
                    # compatible server uses rate=None for native 16 kHz mode.
                    format=_native_rate_audio_pcm(),  # type: ignore[typeddict-item]
                    transcription=AudioTranscriptionParam(
                        model="gpt-4o-transcribe",
                        language=config.REALTIME_TRANSCRIPTION_LANGUAGE,
                    ),
                    turn_detection=turn_detection,
                ),
                output=RealtimeAudioConfigOutputParam(
                    format=_native_rate_audio_pcm(),  # type: ignore[typeddict-item]
                    voice=self.get_current_voice(),
                ),
            ),
            tools=[] if self._tools_disabled else to_realtime_tools_config(tool_specs),
            tool_choice="auto" if not self._tools_disabled else None,
        )

    def _is_connected(self) -> bool:
        """Return whether the realtime connection is open."""
        return self.connection is not None

    def _idle_behavior_ready(self) -> bool:
        """Hold idle behavior while a model response is still active."""
        return self._response_done_event.is_set()

    async def _cancel_partial_transcript_task(self) -> None:
        if self.partial_transcript_task and not self.partial_transcript_task.done():
            self.partial_transcript_task.cancel()
            try:
                await self.partial_transcript_task
            except asyncio.CancelledError:
                pass

    async def change_voice(self, voice: str) -> str:
        """Change only the voice, updating the active session when possible."""
        default_voice = get_default_voice()
        resolved_voice = (
            self._resolve_backend_voice(voice, source="requested voice", fallback=default_voice) or default_voice
        )
        self._voice_override = resolved_voice
        if self.connection is not None:
            try:
                await self.connection.session.update(
                    session=RealtimeSessionCreateRequestParam(
                        type="realtime",
                        audio=RealtimeAudioConfigParam(
                            output=RealtimeAudioConfigOutputParam(
                                voice=resolved_voice,
                            ),
                        ),
                    ),
                )
                return f"Voice changed to {resolved_voice}."
            except Exception as e:
                logger.warning("Failed to update live session for voice change: %s", e)
                return "Voice change failed. Will take effect on next connection."
        return "Voice changed. Will take effect on next connection."

    def get_current_voice(self) -> str:
        """Return the voice currently selected for this handler."""
        default_voice = get_default_voice()
        voice = self._voice_override or get_session_voice(default=default_voice)
        return self._resolve_backend_voice(voice, source="session voice", fallback=default_voice) or default_voice

    async def apply_personality(self, profile: str | None) -> str:
        """Apply a personality to the active or next realtime connection."""
        previous_profile = config.REACHY_MINI_CUSTOM_PROFILE
        set_custom_profile(profile)
        try:
            instructions = f"{get_session_instructions(self.instance_path)}\n\n{SUMMARY_MODE_INSTRUCTIONS}"
            voice = self.get_current_voice()
            core_tools.initialize_tools(force=True)
        except Exception as exc:
            set_custom_profile(previous_profile)
            logger.error("Failed to resolve personality %r: %s", profile, exc)
            return f"Failed to apply personality: {exc}"

        if self.connection is not None:
            try:
                await self.connection.session.update(
                    session=RealtimeSessionCreateRequestParam(
                        type="realtime",
                        instructions=instructions,
                        audio=RealtimeAudioConfigParam(
                            output=RealtimeAudioConfigOutputParam(
                                voice=voice,
                            ),
                        ),
                    ),
                )
                logger.info("Applied personality via live update: %s", profile or "default")
            except Exception as exc:
                logger.warning("Live update failed; will restart session: %s", exc)

            try:
                await self._restart_session()
                return "Applied personality and restarted realtime session."
            except Exception as exc:
                logger.warning("Failed to restart session after apply: %s", exc)
                return "Applied personality. Will take effect on next connection."

        logger.info(
            "Applied personality recorded: %s (no live connection; will apply on next session)",
            profile or "default",
        )
        return "Applied personality. Will take effect on next connection."

    async def _emit_debounced_partial(self, transcript: str, item_id: str, sequence_counter: int) -> None:
        """Emit partial transcript after debounce delay."""
        try:
            await asyncio.sleep(self.partial_debounce_delay)

            input_transcript = self.input_transcript_chunks_by_item
            if input_transcript.item_id == item_id and len(input_transcript.deltas) - 1 == sequence_counter:
                await self.output_queue.put(AdditionalOutputs({"role": "user_partial", "content": transcript}))
                logger.debug(f"Debounced partial emitted: {transcript}")
        except asyncio.CancelledError:
            logger.debug("Debounced partial cancelled")
            raise

    def _record_partial_transcript_delta(
        self,
        input_transcript: InputTranscriptChunksByItem,
        item_id: str,
        delta: str,
    ) -> None:
        """Record a Hugging Face partial transcript snapshot."""
        input_transcript.item_id = item_id
        input_transcript.deltas = [delta]

    async def start_up(self) -> None:
        """Start the handler with minimal retries on unexpected websocket closure."""
        self.client = await self._build_realtime_client()

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await self._run_realtime_session()
                # Normal exit from the session, stop retrying
                return
            except ConnectionClosedError as e:
                # Abrupt close (e.g., "no close frame received or sent") Ã¢â€ â€™ retry
                logger.warning("Realtime websocket closed unexpectedly (attempt %d/%d): %s", attempt, max_attempts, e)
                if attempt < max_attempts:
                    self.client = await self._build_realtime_client()
                    # exponential backoff with jitter
                    base_delay = 2 ** (attempt - 1)  # 1s, 2s, 4s, 8s, etc.
                    jitter = random.uniform(0, 0.5)
                    delay = base_delay + jitter
                    logger.info("Retrying in %.1f seconds...", delay)
                    await asyncio.sleep(delay)
                    continue
                raise
            finally:
                # never keep a stale reference
                self.connection = None
                try:
                    self._connected_event.clear()
                except Exception:
                    pass

    async def _restart_session(self) -> None:
        """Force-close the current session and start a fresh one in background.

        Does not block the caller while the new session is establishing.
        """
        try:
            if self.connection is not None:
                try:
                    await self.connection.close()
                except Exception:
                    pass
                finally:
                    self.connection = None

            # Ensure we have a client (start_up must have run once)
            if getattr(self, "client", None) is None:
                logger.warning("Cannot restart: realtime client not initialized yet.")
                return

            # Fire-and-forget new session and wait briefly for connection
            try:
                self._connected_event.clear()
            except Exception:
                pass
            self.client = await self._build_realtime_client()
            asyncio.create_task(self._run_realtime_session(), name="realtime-session-restart")
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)
                logger.info("Realtime session restarted and connected.")
            except asyncio.TimeoutError:
                logger.warning("Realtime session restart timed out; continuing in background.")
        except Exception as e:
            logger.warning("_restart_session failed: %s", e)

    async def _safe_response_create(self, **kwargs: Any) -> None:
        """Enqueue a response.create() kwargs for the sender worker _response_sender_loop().

        This method never blocks the caller.
        """
        await self._pending_responses.put(kwargs)

    async def say(self, text: str) -> None:
        """Inject ``text`` as a turn and have the model voice it now.

        Mirrors the startup-greeting path: create a user message item, then
        queue a ``response.create`` through the serial sender. Not verbatim TTS
        (speech-to-speech may rephrase). Raises if the session is closed.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("say: empty text")
        if not self.connection:
            raise RuntimeError("say: no active session")
        await self.connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        )
        self._mark_activity("say")
        await self._safe_response_create()

    async def _send_startup_greeting_prompt(self) -> None:
        """Prompt the model to open the conversation once the session is ready."""
        if self._startup_greeting_sent or not self.connection:
            return

        greeting_prompt = get_session_greeting_prompt().strip()
        if not greeting_prompt:
            self._startup_greeting_sent = True
            return

        try:
            await self.connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": greeting_prompt,
                        },
                    ],
                },
            )
            self._startup_greeting_sent = True
            self._mark_activity("startup_greeting_prompt")
            await self._safe_response_create()
            logger.info("Queued startup greeting prompt")
        except Exception as e:
            logger.warning("Failed to queue startup greeting prompt: %s", e)

    async def _response_sender_loop(self) -> None:
        """Dedicated worker that sends ``response.create()`` calls serially.

        This logic was designed to comply with the response.create() docstring specification for event ordering:
        https://github.com/openai/openai-python/blob/3e0c05b84a2056870abf3bd6a5e7849020209cc3/src/openai/resources/realtime/realtime.py#L649C1-L651C30

        For each queued request the worker:
        1. Waits until no response is active (_response_done_event).
        2. Sends response.create().
        3. Waits until the receiver observes response.created or a rejection.
        4. Waits for the response cycle to complete (response.done).
        5. If the server rejected with active_response, retries from step 1.
        """
        while self.connection:
            try:
                kwargs = await self._pending_responses.get()
            except asyncio.CancelledError:
                return

            # Parallel tool calls enqueue duplicate empty requests; coalesce to one.
            while not kwargs and not self._pending_responses.empty():
                try:
                    self._pending_responses.get_nowait()
                except asyncio.QueueEmpty:
                    break

            sent = False
            max_retries = 5
            attempts = 0
            while not sent and self.connection and attempts < max_retries:
                try:
                    await asyncio.wait_for(
                        self._response_done_event.wait(),
                        timeout=_RESPONSE_DONE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("Timed out waiting for previous response to finish; forcing ahead")
                    self._response_done_event.set()

                if not self.connection:
                    break

                self._last_response_rejected = False
                self._response_started_or_rejected_event.clear()
                try:
                    await self.connection.response.create(**kwargs)
                except Exception as e:
                    logger.debug("_response_sender_loop: send failed: %s", e)
                    self._response_done_event.set()
                    break

                try:
                    await asyncio.wait_for(
                        self._response_started_or_rejected_event.wait(),
                        timeout=_RESPONSE_DONE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("Timed out waiting for response.created or response rejection")

                # Check if the receiver loop observed an asynchronous rejection.
                if self._last_response_rejected:
                    attempts += 1
                    if attempts >= max_retries:
                        logger.debug("response.create rejected %d times; giving up", attempts)
                        break
                    logger.debug("response.create was rejected; retrying (%d/%d)", attempts, max_retries)
                    await asyncio.sleep(_RESPONSE_REJECTION_RETRY_DELAY)
                    continue

                try:
                    await asyncio.wait_for(
                        self._response_done_event.wait(),
                        timeout=_RESPONSE_DONE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("Timed out waiting for response.done; assuming response completed")
                    self._response_done_event.set()
                    break

                sent = True

    async def _handle_tool_result(self, completed_tool: ToolNotification) -> None:
        """Process the result of a tool call."""
        if completed_tool.error is not None:
            logger.error(
                "Tool '%s' (id=%s) failed with error: %s",
                completed_tool.tool_name,
                completed_tool.id,
                completed_tool.error,
            )
            tool_result = {"error": completed_tool.error}
            tool_result_for_model = tool_result
        elif completed_tool.result is not None:
            tool_result = completed_tool.result
            tool_result_for_model = (
                self._sanitize_tool_result_for_model(completed_tool.tool_name, tool_result)
                if isinstance(tool_result, dict)
                else tool_result
            )
            logger.info(
                "Tool '%s' (id=%s) executed successfully.",
                completed_tool.tool_name,
                completed_tool.id,
            )
            logger.debug("Tool '%s' model-visible result: %s", completed_tool.tool_name, tool_result_for_model)
        else:
            logger.warning(
                "Tool '%s' (id=%s) returned no result and no error", completed_tool.tool_name, completed_tool.id
            )
            tool_result = {"error": "No result returned from tool execution"}
            tool_result_for_model = tool_result

        # Connection may have closed while tool was running
        if not self.connection:
            logger.warning(
                "Connection closed during tool '%s' (id=%s) execution; cannot send result back",
                completed_tool.tool_name,
                completed_tool.id,
            )
            return

        try:
            send_result_to_model = not completed_tool.is_idle_tool_call
            if send_result_to_model:
                self._mark_activity("tool_result_ready")
            model_result_submitted = False
            if send_result_to_model and isinstance(completed_tool.id, str):
                if not await self._wait_for_response_done_before_tool_result():
                    send_result_to_model = False
                if not send_result_to_model:
                    logger.warning(
                        "Dropping realtime model result for tool '%s' (id=%s) because response.done was not observed",
                        completed_tool.tool_name,
                        completed_tool.id,
                    )
                elif not self.connection:
                    logger.warning(
                        "Connection closed before sending tool '%s' (id=%s) result back",
                        completed_tool.tool_name,
                        completed_tool.id,
                    )
                    return
                else:
                    await self.connection.conversation.item.create(
                        item={
                            "type": "function_call_output",
                            "call_id": completed_tool.id,
                            "output": json.dumps(tool_result_for_model),
                        },
                    )
                    model_result_submitted = True

            await self.output_queue.put(
                AdditionalOutputs(
                    {
                        "role": "assistant",
                        "content": json.dumps(tool_result_for_model),
                    },
                ),
            )

            if model_result_submitted and completed_tool.tool_name == "camera" and "b64_im" in tool_result:
                # use raw base64, don't json.dumps (which adds quotes)
                b64_im = tool_result["b64_im"]
                if not isinstance(b64_im, str):
                    logger.warning("Unexpected type for b64_im: %s", type(b64_im))
                    b64_im = str(b64_im)
                image_width = tool_result.get("image_width")
                image_height = tool_result.get("image_height")
                jpeg_bytes_value = tool_result.get("jpeg_bytes")
                jpeg_bytes = jpeg_bytes_value if isinstance(jpeg_bytes_value, int) else (len(b64_im) * 3) // 4
                await self.connection.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{b64_im}",
                            },
                        ],
                    },
                )
                if isinstance(image_width, int) and isinstance(image_height, int):
                    logger.info(
                        "Added camera image to conversation frame=%sx%s jpeg_bytes=%s",
                        image_width,
                        image_height,
                        jpeg_bytes,
                    )
                else:
                    logger.info(
                        "Added camera image to conversation jpeg_bytes=%s",
                        jpeg_bytes,
                    )

            if isinstance(completed_tool.id, str):
                self._in_flight_tool_calls.discard(completed_tool.id)

            tool = core_tools.get_tools().get(completed_tool.tool_name)
            # Always surface errors, skip the spoken follow-up for tools that opt out.
            if model_result_submitted and (completed_tool.error is not None or tool is None or tool.needs_response):
                self._tool_batch_needs_response = True

            # Parallel tool calls in one turn: respond once every result is in, not per tool.
            if self._tool_batch_needs_response and not self._in_flight_tool_calls:
                self._tool_batch_needs_response = False
                await self._safe_response_create()

        except ConnectionClosedError:
            logger.warning("Connection closed while sending tool result")
            self.connection = None
            self._response_done_event.set()

    async def _run_realtime_session(self) -> None:
        """Establish and manage a single realtime session."""
        tool_specs = get_tool_specs()
        logger.info(
            "Tools to be used in conversation: %s",
            [tool["name"] for tool in tool_specs],
        )
        connect_kwargs: dict[str, Any] = {}
        if self._realtime_connect_query:
            connect_kwargs["extra_query"] = self._realtime_connect_query
        async with self.client.realtime.connect(**connect_kwargs) as conn:
            try:
                session_config = self._get_session_config(tool_specs)
                await conn.session.update(session=session_config)
                logger.info(
                    "Realtime session initialized with profile=%r voice=%r",
                    getattr(config, "REACHY_MINI_CUSTOM_PROFILE", None),
                    self.get_current_voice(),
                )
            except Exception:
                logger.exception("Realtime session.update failed; aborting startup")
                raise

            logger.info("Realtime session updated successfully")

            # Reset the partial-transcript accumulator for each new session
            self.input_transcript_chunks_by_item = InputTranscriptChunksByItem()
            # A fresh session has no suppressed responses to re-request.
            self._suppressed_response = False
            self._suppressed_response_id = None

            # Manage events received from the realtime server.
            self.connection = conn
            try:
                self._connected_event.set()
            except Exception:
                pass

            response_sender_task: asyncio.Task[None] | None = None
            try:
                # Start the background tool manager
                self.tool_manager.start_up(tool_callbacks=[self._handle_tool_result])

                # Start the response sender worker
                response_sender_task = asyncio.create_task(self._response_sender_loop(), name="response-sender")
                await self._send_startup_greeting_prompt()

                async for event in self.connection:
                    logger.debug("Realtime event: %s", event.type)
                    if event.type == "input_audio_buffer.speech_started":
                        self._mark_activity("user_speech_started")
                        self._turn_user_done_at = None
                        self._turn_response_created_at = None
                        self._turn_first_audio_at = None
                        if self._clear_queue:
                            self._clear_queue()
                        self.deps.movement_manager.set_listening(True)
                        logger.debug("User speech started")

                    if event.type == "input_audio_buffer.speech_stopped":
                        self._mark_activity("user_speech_stopped")
                        self.deps.movement_manager.set_listening(False)
                        logger.debug("User speech stopped - server will auto-commit with VAD")

                    if event.type == "response.output_audio.done":
                        self.deps.movement_manager.set_speaking(False)
                        logger.debug("response completed")

                    if event.type == "response.output_text.delta":
                        logger.debug("response text delta")

                    if event.type == "response.output_text.done":
                        logger.debug("response text done: %s", event.text)
                        if event.text:
                            self._emit_text(event.text)

                    if event.type == "response.created":
                        if push_to_talk_enabled() and self.push_to_talk_holding:
                            # A pause triggered server VAD while the user still
                            # holds the talk button. Mark this response as
                            # suppressed (its audio is dropped below), cancel it
                            # and keep listening. On button release commit_input
                            # re-requests a fresh response.
                            self._suppressed_response = True
                            self._suppressed_response_id = getattr(getattr(event, "response", None), "id", None)
                            logger.info(
                                "Suppressing response %s started while talk button held",
                                self._suppressed_response_id or "?",
                            )
                            try:
                                await self.connection.response.cancel()
                            except Exception as e:
                                logger.debug("response.cancel failed: %s", e)
                            if self._clear_queue:
                                self._clear_queue()
                            continue
                        self._mark_activity("response_created")
                        self.deps.movement_manager.set_speaking(True)
                        self._response_done_event.clear()
                        self._response_started_or_rejected_event.set()
                        if self._turn_user_done_at is not None and self._turn_response_created_at is None:
                            self._turn_response_created_at = time.perf_counter()
                            delta_ms = (self._turn_response_created_at - self._turn_user_done_at) * 1000
                            logger.info("Turn latency: response.created %.0f ms after user transcript", delta_ms)
                        logger.debug("Response created (active)")

                    if event.type == "response.done":
                        # Doesn't mean the audio is done playing
                        # Resume tracking for responses that emit no audio (text-only / tool-only).
                        self.deps.movement_manager.set_speaking(False)
                        self._response_done_event.set()
                        self._response_started_or_rejected_event.set()
                        self._mark_activity("response.done")
                        if (
                            self._suppressed_response_id is not None
                            and getattr(getattr(event, "response", None), "id", None) == self._suppressed_response_id
                        ):
                            self._suppressed_response_id = None
                        logger.debug("Response done")

                    if event.type == "conversation.item.input_audio_transcription.delta":
                        self._mark_activity("user_transcription_delta")
                        logger.debug(f"User partial transcript: {event.delta}")

                        item_id = event.item_id
                        delta = event.delta or ""

                        input_transcript = self.input_transcript_chunks_by_item
                        self._record_partial_transcript_delta(input_transcript, item_id, delta)

                        current_partial = "".join(input_transcript.deltas)
                        sequence_counter = len(input_transcript.deltas) - 1

                        await self._cancel_partial_transcript_task()

                        # Start new debounce timer with the last delta
                        self.partial_transcript_task = asyncio.create_task(
                            self._emit_debounced_partial(current_partial, item_id, sequence_counter)
                        )

                    # Handle completed transcription (user finished speaking)
                    if event.type == "conversation.item.input_audio_transcription.completed":
                        self._mark_activity("user_transcription_completed")
                        raw_transcript = event.transcript or ""
                        transcript = raw_transcript.strip()
                        logger.debug("User transcript: %s", raw_transcript)
                        self.deps.movement_manager.set_listening(False)

                        await self._cancel_partial_transcript_task()

                        if not transcript:
                            logger.debug("Ignoring empty user transcript")
                            continue

                        self._turn_user_done_at = time.perf_counter()
                        self._turn_response_created_at = None
                        self._turn_first_audio_at = None
                        self._in_flight_tool_calls.clear()
                        self._tool_batch_needs_response = False

                        await self.output_queue.put(AdditionalOutputs({"role": "user", "content": transcript}))
                        self._emit_transcript("user", transcript, True)

                    # Handle assistant transcription
                    if event.type == "response.output_audio_transcript.done":
                        if self._suppressed_response_holds(event):
                            continue
                        self._mark_activity("assistant_transcript_done")
                        logger.debug(f"Assistant transcript: {event.transcript}")
                        await self.output_queue.put(
                            AdditionalOutputs({"role": "assistant", "content": event.transcript})
                        )
                        self._emit_transcript("assistant", event.transcript or "", True)

                    # Handle audio delta
                    if event.type == "response.output_audio.delta":
                        if self._suppressed_response_holds(event):
                            continue
                        decoded_pcm_bytes = base64.b64decode(event.delta)
                        decoded_pcm = np.frombuffer(decoded_pcm_bytes, dtype=np.int16).reshape(1, -1)
                        self._mark_activity("assistant_audio_delta")
                        if self._turn_user_done_at is not None and self._turn_first_audio_at is None:
                            self._turn_first_audio_at = time.perf_counter()
                            delta_ms = (self._turn_first_audio_at - self._turn_user_done_at) * 1000
                            logger.info("Turn latency: first audio delta %.0f ms after user transcript", delta_ms)
                        await self.output_queue.put(
                            (
                                self.SAMPLE_RATE,
                                decoded_pcm,
                            ),
                        )
                    # ---- tool-calling plumbing ----
                    if event.type == "response.function_call_arguments.done":
                        self._mark_activity("tool_call_received")
                        tool_name = getattr(event, "name", None)
                        args_json_str = getattr(event, "arguments", None)
                        call_id: str = str(getattr(event, "call_id", uuid.uuid4()))

                        logger.info(
                            "Tool call received Ã¢â‚¬â€ tool_name=%r, call_id=%s, args=%s",
                            tool_name,
                            call_id,
                            args_json_str,
                        )

                        if not isinstance(tool_name, str) or not isinstance(args_json_str, str):
                            logger.error(
                                "Invalid tool call: tool_name=%s (type=%s), args=%s (type=%s), call_id=%s",
                                tool_name,
                                type(tool_name).__name__,
                                args_json_str,
                                type(args_json_str).__name__,
                                call_id,
                            )
                            continue

                        self._in_flight_tool_calls.add(call_id)
                        background_tool = await self.tool_manager.start_tool(
                            call_id=call_id,
                            tool_call_routine=ToolCallRoutine(
                                tool_name=tool_name,
                                args_json_str=args_json_str,
                                deps=self.deps,
                            ),
                            is_idle_tool_call=False,
                        )

                        await self.output_queue.put(
                            AdditionalOutputs(
                                {
                                    "role": "assistant",
                                    "content": f"Ã°Å¸â€ºÂ Ã¯Â¸Â Used tool {tool_name} with args {args_json_str}. The tool is now running. Tool ID: {background_tool.tool_id}",
                                },
                            ),
                        )
                        logger.info(
                            "Started background tool: %s (id=%s, call_id=%s)",
                            tool_name,
                            background_tool.tool_id,
                            call_id,
                        )

                    # server error
                    if event.type == "error":
                        err = getattr(event, "error", None)
                        msg = getattr(err, "message", str(err) if err else "unknown error")
                        code = getattr(err, "code", "") or getattr(err, "type", "")

                        if code == "conversation_already_has_active_response":
                            # response.create was rejected.  The sender worker
                            # is waiting on _response_done_event; when the active
                            # response finishes it will wake up and see this flag.
                            self._last_response_rejected = True
                            self._response_started_or_rejected_event.set()
                            logger.debug("response.create rejected; worker will retry after active response finishes")
                        else:
                            self._response_started_or_rejected_event.set()
                            logger.error("Realtime error [%s]: %s (raw=%s)", code, msg, err)

                        if code == "input_audio_buffer_commit_empty":
                            self.deps.movement_manager.set_listening(False)
                            self._emit_notice(
                                "empty_commit",
                                "El audio no se pudo transcribir. MantÃ©n el botÃ³n un momento y vuelve a intentarlo.",
                            )

                        # The hosted backend may run a model that rejects
                        # function tools (e.g. "Function tools with
                        # reasoning_effort are not supported for ..."). Degrade
                        # gracefully: disable tools for the session and retry
                        # the current turn so the conversation keeps working.
                        if code == "response_failed" and "reasoning_effort" in msg and not self._tools_disabled:
                            self._tools_disabled = True
                            logger.warning(
                                "Backend rejected function tools (reasoning_effort); disabling tools for this session"
                            )
                            try:
                                await self.connection.session.update(
                                    session=RealtimeSessionCreateRequestParam(
                                        type="realtime",
                                        tools=[],
                                    ),
                                )
                            except Exception as e:
                                logger.debug("Failed to disable tools after backend rejection: %s", e)
                            await self._safe_response_create()

                        # Only show user-facing errors, not internal state errors.
                        if code not in (
                            "input_audio_buffer_commit_empty",
                            "conversation_already_has_active_response",
                            "response_failed",
                        ):
                            await self.output_queue.put(
                                AdditionalOutputs({"role": "assistant", "content": f"[error] {msg}"})
                            )
            finally:
                # Stop the response sender worker.
                if response_sender_task is not None:
                    response_sender_task.cancel()
                    try:
                        await response_sender_task
                    except asyncio.CancelledError:
                        pass

                # Stop background tool manager tasks (listener + cleanup) in all paths.
                await self.tool_manager.shutdown()

    # Microphone receive
    def _suppressed_response_holds(self, event: Any) -> bool:
        """Return whether an output event belongs to a suppressed push-to-talk response."""
        if not push_to_talk_enabled():
            return False
        if self.push_to_talk_holding:
            return True
        if self._suppressed_response_id is not None:
            event_id = getattr(event, "response_id", None) or getattr(getattr(event, "response", None), "id", None)
            if event_id == self._suppressed_response_id:
                return True
        return False

    async def commit_input(self) -> None:
        """Commit the buffered input audio, clearing the server input buffer."""
        if not self.connection:
            return
        try:
            await self.connection.input_audio_buffer.commit()
            self._mark_activity("push_to_talk_commit")
        except Exception as e:
            logger.debug("input_audio_buffer.commit failed: %s", e)

    async def release_turn(self, frames: list[AudioFrame]) -> None:
        """Flush the talk-button audio to the backend and request the response.

        Con streaming en vivo el audio ya llegó al backend mientras se
        escuchaba; aquí se hace commit del buffer (que finaliza la transcripción)
        y, si hubo una respuesta suprimida durante la escucha, se re-pide.
        """
        if not self.connection:
            return
        if frames:
            for frame in frames:
                await self.receive(frame)
        await self.commit_input()
        if self._suppressed_response:
            self._suppressed_response = False
            # Wait for the cancelled response's response.done first, or the
            # server rejects the new response.create with
            # conversation_already_has_active_response.
            for _ in range(40):
                if self._suppressed_response_id is None:
                    break
                await asyncio.sleep(0.1)
            logger.info("Re-requesting response after push-to-talk release")
            await self._safe_response_create()
        else:
            # Sin VAD el servidor no auto-responde tras el commit: pedirlo aquí.
            await self._safe_response_create()

    async def _say_verbatim(self, text: str) -> None:
        """Ask the realtime session to read ``text`` out loud, word for word."""
        instruction = (
            "Lee en voz alta EXACTAMENTE el siguiente texto, palabra por palabra, "
            "respetando la puntuación, sin comentarios, sin resúmenes y sin añadir nada. "
            f"Texto a leer: «{text}»"
        )
        await self.say(instruction)

    async def read_text(self, text: str) -> str:
        """Read ``text`` verbatim with the current voice via the backend TTS endpoint.

        Synthesizes with the OpenAI-compatible ``/audio/speech`` endpoint using
        the same voice as the realtime session, then enqueues the PCM on the
        output queue so the play loop pushes it to the robot speaker (with the
        wobbler providing speech-synced head motion). Falls back to ``say()``
        with a verbatim instruction when the endpoint is unavailable.
        """
        if not self.connection:
            return "no_session"
        voice = self.get_current_voice()
        try:
            speech = await self.client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=text,
                response_format="wav",
            )
        except Exception as error:
            logger.warning("TTS endpoint unavailable (%s); falling back to verbatim say()", error)
            await self._say_verbatim(text)
            return "say_fallback"
        rate, pcm = _wav_to_pcm16(speech.content)
        if pcm is None or pcm.size == 0:
            logger.warning("Empty TTS audio; falling back to verbatim say()")
            await self._say_verbatim(text)
            return "say_fallback"
        if rate != 16000:
            pcm = _resample_pcm16(pcm, rate, 16000)
        await self.output_queue.put((16000, pcm))
        self._mark_activity("assistant_tts")
        return "ok"

    async def clear_session(self) -> None:
        """Clear the backend conversation history (new conversation)."""
        if not self.connection:
            return
        try:
            await self.connection.conversation.clear()
            self._mark_activity("session_cleared")
            logger.info("Conversation history cleared")
        except Exception as e:
            logger.warning("Failed to clear conversation: %s", e)

    async def receive(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Receive audio frame from the microphone and send it to the realtime server.

        Handles both mono and stereo audio formats, converting to the expected
        mono format for the realtime API.

        Args:
            frame: A tuple containing (sample_rate, audio_data).

        """
        if not self.connection:
            return

        _, audio_frame = frame
        if audio_frame.size == 0:
            return

        # Reshape if needed
        if audio_frame.ndim == 2:
            # channels-last convention
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            # Multiple channels -> Mono channel
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]

        # Cast if needed
        audio_frame = audio_to_int16(audio_frame)

        # Send to the realtime input buffer (guard against races during reconnect).
        try:
            audio_message = base64.b64encode(audio_frame.tobytes()).decode("utf-8")
            await self.connection.input_audio_buffer.append(audio=audio_message)
        except Exception as e:
            logger.debug("Dropping audio frame: connection not ready (%s)", e)
            return

    async def shutdown(self) -> None:
        """Shutdown the handler."""
        # Unblock the response sender worker so it can exit
        self._response_done_event.set()

        # Stop background tool manager tasks (listener + cleanup)
        await self.tool_manager.shutdown()

        await self._cancel_partial_transcript_task()

        if self.connection:
            try:
                await self.connection.close()
            except ConnectionClosedError as e:
                logger.debug(f"Connection already closed during shutdown: {e}")
            except Exception as e:
                logger.debug(f"connection.close() ignored: {e}")
            finally:
                self.connection = None

        # Clear any remaining items in the output queue
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def get_available_voices(self) -> list[str]:
        """Return the available Hugging Face voices."""
        return get_available_voices()

    async def _build_realtime_client(self) -> AsyncOpenAI:
        """Build the Hugging Face OpenAI-compatible realtime client."""
        configured_bearer_token = (config.HF_TOKEN or "").strip()
        connection_selection = get_hf_connection_selection()
        direct_realtime_url = get_hf_direct_ws_url()
        if connection_selection.mode == HF_LOCAL_CONNECTION_MODE:
            if not direct_realtime_url:
                raise RuntimeError("HF_REALTIME_WS_URL must be set when HF_REALTIME_CONNECTION_MODE=local")
            client, connect_query = _build_openai_compatible_client_from_realtime_url(
                direct_realtime_url,
                configured_bearer_token,
            )
            self._realtime_connect_query = connect_query
            logger.info("Using direct Hugging Face realtime endpoint %s", direct_realtime_url)
            return client

        session_url = connection_selection.session_url
        if not session_url:
            raise RuntimeError("Built-in Hugging Face session proxy URL is unavailable")
        if direct_realtime_url:
            logger.info("HF_REALTIME_CONNECTION_MODE=deployed; ignoring HF_REALTIME_WS_URL.")

        bearer_token = configured_bearer_token or (get_token() or "").strip()
        allocator_headers = {"User-Agent": "reachy-mini-push-to-talk-app"}
        if bearer_token:
            allocator_headers["X-Reachy-Mini-Authorization"] = f"Bearer {bearer_token}"
        allocator_payload: dict[str, str] = {}
        try:
            hardware_id = self.deps.reachy_mini.client.get_status(wait=False).hardware_id
        except (AssertionError, ConnectionError, TimeoutError) as e:
            logger.warning("Daemon status unavailable for realtime session allocation: %s", e)
        else:
            if hardware_id:
                allocator_payload["hardware_id"] = hardware_id

        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.post(session_url, headers=allocator_headers, json=allocator_payload)
            response.raise_for_status()
            payload = response.json()

        connect_url = payload.get("connect_url")
        if not isinstance(connect_url, str) or not connect_url:
            raise RuntimeError(f"Session allocator response did not contain a valid connect_url: {payload!r}")

        parsed_connect_url = parse_hf_realtime_url(connect_url)
        if not parsed_connect_url.has_realtime_path:
            raise ValueError(f"Expected realtime connect URL ending with /realtime, got: {connect_url}")

        logger.info("Allocated realtime session %s", payload.get("session_id") or "<unknown>")
        client, connect_query = _build_openai_compatible_client_from_realtime_url(
            connect_url,
            bearer_token,
        )
        self._realtime_connect_query = connect_query
        return client
