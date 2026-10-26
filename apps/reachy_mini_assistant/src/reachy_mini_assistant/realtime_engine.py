"""Motor del Hub para el asistente: cliente realtime OpenAI-compatible
contra el backend de Hugging Face (mismo que usa reachy_mini_hub).

Piensa con el motor del Hub (server_vad) y entrega el TEXTO de la respuesta
(para sintetizarlo con ElevenLabs).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Callable
from urllib.parse import parse_qsl, urlunsplit, urlsplit

from openai import AsyncOpenAI
from openai.types.realtime import (  # type: ignore
    AudioTranscriptionParam,
    RealtimeAudioConfigInputParam,
    RealtimeAudioConfigOutputParam,
    RealtimeAudioConfigParam,
)
from openai.types.realtime.realtime_audio_input_turn_detection_param import ServerVad  # type: ignore

from reachy_mini_assistant import tools

logger = logging.getLogger("reachy_mini_assistant.realtime")

DEFAULT_SESSION_URL = "https://pollen-robotics-reachy-mini-realtime-url.hf.space/session"


def _parse_realtime_url(url: str) -> tuple[str, str, dict]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    path = parsed.path.rstrip("/")
    has_realtime = path.endswith("/realtime")
    base_path = path[: -len("/realtime")] if has_realtime else path
    connect_query = {
        k: v for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "model"
    }
    http_scheme = "https" if scheme in {"wss", "https"} else "http"
    ws_scheme = "wss" if scheme in {"wss", "https"} else "ws"
    base_url = urlunsplit((http_scheme, parsed.netloc, base_path, "", ""))
    ws_base = urlunsplit((ws_scheme, parsed.netloc, base_path, "", ""))
    return base_url, ws_base, connect_query


def _take_chunk(buffer: str, min_len: int = 16, max_len: int = 140) -> tuple[str, str]:
    """Toma un fragmento (termina en . ! ? , ; : ¡ ¿) para hablar por partes."""
    buffer = (buffer or "").lstrip()
    if len(buffer) < min_len:
        return "", buffer
    idx = -1
    for i, ch in enumerate(buffer):
        if ch in ".!?,;:¡¿" and i + 1 >= min_len:
            idx = i + 1
    if idx >= min_len and idx <= max_len:
        return buffer[:idx].strip(), buffer[idx:].lstrip()
    if len(buffer) >= max_len:
        cut = buffer.rfind(" ", 0, max_len)
        if cut < min_len:
            cut = max_len
        return buffer[:cut].strip(), buffer[cut:].lstrip()
    return "", buffer


def _hf_token() -> str | None:
    """Lee el token de Hugging Face (env o cache del robot)."""
    import os

    token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGING_FACE_HUB_TOKEN", "").strip()
    if token:
        return token
    try:
        path = os.path.expanduser("~/.cache/huggingface/token")
        if os.path.exists(path):
            value = open(path, encoding="utf-8").read().strip()
            if value:
                return value
    except Exception:
        pass
    return None


class RealtimeEngine:
    """Conexión al backend realtime del Hub (Hugging Face, OpenAI-compatible)."""

    def __init__(
        self,
        session_url: str,
        instructions: str,
        language: str = "es",
        output_voice: str = "Aiden",
        tool_specs_param: list[dict] | None = None,
        hardware_id: str = "",
        on_text_chunk: Callable[[str], Any] | None = None,
        on_user_text: Callable[[str], Any] | None = None,
        on_state: Callable[[str], Any] | None = None,
        on_ack: Callable[[str], Any] | None = None,
        on_notify: Callable[[str], Any] | None = None,
    ):
        self.session_url = session_url or DEFAULT_SESSION_URL
        self.instructions = instructions
        self.language = language
        self.output_voice = output_voice
        self.hardware_id = hardware_id
        self.tool_specs = tool_specs_param if tool_specs_param else tools.TOOLS
        self.on_text_chunk = on_text_chunk
        self.on_user_text = on_user_text
        self.on_state = on_state
        self.on_ack = on_ack          # async: aviso hablado inmediato (en el bucle)
        self.on_notify = on_notify    # síncrono + hilo-seguro: narrar desde hilos bg
        self.output_stopped = False
        self.cancelled_responses = set()
        self.active_response = None
        self.conn = None
        self.connected = asyncio.Event()
        self._stream_text = ""
        self._text_emitted = False
        self._ack_spoken = False  # True: ya hablamos el aviso; silenciar el turno del modelo
        self._seen_event_types: set[str] = set()
        # Transcripción en vivo (del motor del Hub) para mostrar en el widget.
        self.user_text = ""
        self.assistant_text = ""

    def _session_config(self) -> dict:
        instructions = (self.instructions or "").rstrip()
        # El modelo realtime tiende a responder en inglés si no se lo prohíbe.
        if (self.language or "es").lower().startswith("es"):
            if instructions:
                instructions += "\n\n"
            instructions += (
                "Responde SIEMPRE en español. No saludes. No traduzcas al inglés. "
                "Habla con naturalidad y cercanía."
            )
        return {
            "type": "realtime",
            "instructions": instructions,
            "audio": RealtimeAudioConfigParam(
                input=RealtimeAudioConfigInputParam(
                    format={"type": "audio/pcm", "rate": None},
                    transcription=AudioTranscriptionParam(
                        model="gpt-4o-transcribe",
                        language=self.language,
                    ),
                    turn_detection=ServerVad(
                        type="server_vad",
                        interrupt_response=True,
                        silence_duration_ms=250,
                        prefix_padding_ms=150,
                    ),
                ),
                output=RealtimeAudioConfigOutputParam(
                    format={"type": "audio/pcm", "rate": None},
                    voice=self.output_voice,
                ),
            ),
            "tools": self.tool_specs,
            "tool_choice": "auto",
        }

    async def _allocate(self, session_url: str, token: str | None) -> str:
        """Pide al asignador de sesión el connect_url del backend realtime."""
        import httpx

        headers = {"User-Agent": "reachy-mini-assistant"}
        if token:
            headers["X-Reachy-Mini-Authorization"] = f"Bearer {token}"
        payload: dict[str, str] = {}
        if self.hardware_id:
            payload["hardware_id"] = self.hardware_id
        async with httpx.AsyncClient(timeout=20.0) as http_client:
            response = await http_client.post(session_url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        connect_url = data.get("connect_url") if isinstance(data, dict) else None
        if not isinstance(connect_url, str) or not connect_url:
            raise RuntimeError(f"El asignador no devolvió connect_url: {data!r}")
        logger.info("Sesión realtime asignada: %s", data.get("session_id") or "<unknown>")
        return connect_url

    async def run(self) -> None:
        token = _hf_token()
        connect_url = await self._allocate(self.session_url, token)
        base_url, ws_base, connect_query = _parse_realtime_url(connect_url)
        client = AsyncOpenAI(
            api_key=token or "DUMMY",
            base_url=base_url,
            websocket_base_url=ws_base,
        )
        kwargs: dict[str, Any] = {}
        if connect_query:
            kwargs["extra_query"] = connect_query
        try:
            async with client.realtime.connect(**kwargs) as conn:
                self.conn = conn
                await conn.session.update(session=self._session_config())
                self.connected.set()
                logger.info("Motor del Hub conectado (server_vad activo).")
                async for event in conn:
                    await self._handle(conn, event)
        except Exception as e:  # noqa: BLE001
            logger.warning("Motor del Hub terminó: %s", e)
        finally:
            self.conn = None
            self.connected.clear()
            await client.close()

    @staticmethod
    async def _maybe_await(value: Any) -> None:
        if value is None:
            return
        if asyncio.iscoroutine(value) or asyncio.isfuture(value):
            await value

    async def _handle(self, conn, event) -> None:
        t = getattr(event, "type", "")
        # Registrar tipos de eventos vistos (una vez cada uno) para diagnóstico.
        if t not in self._seen_event_types:
            self._seen_event_types.add(t)
            logger.info("Evento realtime visto: %s", t)
        try:
            rid=getattr(event,'response_id',None)
            if rid in self.cancelled_responses: return
            if t == 'input_audio_buffer.speech_started': self.output_stopped=False
            if t == 'response.created':
                self.active_response=getattr(getattr(event,'response',None),'id',None)
                if self.output_stopped:
                    await self.cancel_response()
                    return
            if self.output_stopped and t.startswith('response.'): return
            if t in ("response.output_text.delta", "response.output_audio_transcript.delta"):
                # Texto EN TIEMPO REAL del motor del Hub: hablar por partes.
                delta = getattr(event, "delta", "") or ""
                self._stream_text += delta
                if t == "response.output_audio_transcript.delta":
                    self.assistant_text += delta
                # Si ya hablamos el aviso (abrir/cerrar app), silenciar este turno.
                if self._ack_spoken:
                    self._stream_text = ""
                    return
                chunk, rest = _take_chunk(self._stream_text)
                self._stream_text = rest
                if chunk and self.on_text_chunk:
                    self._text_emitted = True
                    await self._maybe_await(self.on_text_chunk(chunk))
            elif t == "response.output_text.done":
                if self._ack_spoken:
                    self._stream_text = ""
                elif self._stream_text and self.on_text_chunk:
                    self._text_emitted = True
                    await self._maybe_await(self.on_text_chunk(self._stream_text))
                self._stream_text = ""
            elif t == "response.output_audio_transcript.done":
                # Flushear el resto del texto ya emitido por deltas;
                # si no hubo deltas, usar el transcript completo.
                if self._ack_spoken:
                    self._stream_text = ""
                elif self._stream_text and self.on_text_chunk:
                    await self._maybe_await(self.on_text_chunk(self._stream_text))
                elif not self._text_emitted and self.on_text_chunk:
                    text = (event.transcript or "").strip()
                    if text:
                        await self._maybe_await(self.on_text_chunk(text))
                self._stream_text = ""
            elif t == "response.function_call_arguments.done":
                await self._handle_tool(conn, event)
            elif t == "input_audio_buffer.speech_started":
                self._text_emitted = False
                self._stream_text = ""
                self.user_text = ""
                self.assistant_text = ""
                self._ack_spoken = False
                if self.on_state:
                    await self._maybe_await(self.on_state("listening"))
            elif t == "input_audio_buffer.speech_stopped":
                if self.on_state:
                    await self._maybe_await(self.on_state("thinking"))
            elif t == "response.created":
                self._text_emitted = False
                self._stream_text = ""
                self.assistant_text = ""
                if self.on_state:
                    await self._maybe_await(self.on_state("thinking"))
            elif t == "response.done":
                self._ack_spoken = False
                if self.on_state:
                    await self._maybe_await(self.on_state("idle"))
            elif t == "conversation.item.input_audio_transcription.delta":
                # Transcripción parcial de lo que dices (en vivo).
                delta = getattr(event, "delta", None)
                if delta:
                    self.user_text += delta
                else:
                    cumulative = getattr(event, "transcript", None)
                    if cumulative:
                        self.user_text = cumulative
            elif t == "conversation.item.input_audio_transcription.completed":
                final = (getattr(event, "transcript", None) or "").strip()
                if final:
                    self.user_text = final
                if self.on_user_text and final:
                    await self._maybe_await(self.on_user_text(final))
        except Exception as e:  # noqa: BLE001
            logger.warning("Error manejando evento realtime %s: %s", t, e)

    async def _handle_tool(self, conn, event) -> None:
        name = getattr(event, "name", "") or ""
        try:
            args = json.loads(getattr(event, "arguments", "{}") or "{}")
        except Exception:
            args = {}
        # Aviso inmediato hablado antes de abrir/cerrar (no esperar a la app).
        if name == "abrir_app" and self.on_ack:
            nombre = str(args.get("nombre") or "").strip() or "la aplicación"
            self._ack_spoken = True
            await self._maybe_await(self.on_ack(f"Abriendo {nombre}, dame un segundo."))
        elif name == "cerrar_app" and self.on_ack:
            self._ack_spoken = True
            await self._maybe_await(self.on_ack("Cerrando la aplicación, dame un segundo."))
        # Ejecutar en hilo: abrir/c tarda (espera del portal) y no debe
        # bloquear el bucle de eventos del motor.
        result = await asyncio.to_thread(tools.execute, name, args, self.on_notify)
        call_id = getattr(event, "call_id", None)
        output = result.get("result_text", "")
        if conn and call_id:
            try:
                await conn.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(output, ensure_ascii=False),
                    }
                )
                await conn.response.create()
            except Exception as e:  # noqa: BLE001
                logger.warning("No se pudo enviar resultado de tool %s: %s", name, e)

    async def cancel_response(self):
        self.output_stopped=True
        self._stream_text=''
        if self.active_response:
            self.cancelled_responses.add(self.active_response)
        if self.conn:
            try:
                await asyncio.wait_for(self.conn.response.cancel(),2)
                await asyncio.wait_for(self.conn.input_audio_buffer.clear(),2)
            except Exception:
                pass

    async def send_audio(self, pcm_int16: bytes) -> None:
        conn = self.conn
        if conn is None:
            return
        try:
            await conn.input_audio_buffer.append(
                audio=base64.b64encode(pcm_int16).decode("ascii")
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Enviando audio: %s", e)
