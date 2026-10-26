"""TTS enchufable: piper (local) y azure (es-CO colombiano).

Devuelve (sample_rate, float32 mono).
"""

from __future__ import annotations

import asyncio
import io
import os
import struct
import subprocess
import sys
import tempfile
import wave

import httpx
import numpy as np

from reachy_mini_assistant.config import AssistantConfig

OUT_RATE = 16000


def _read_wav_pcm(path: str) -> tuple[int, np.ndarray]:
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        data = w.readframes(n)
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return rate, audio


def _resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or audio.size == 0:
        return audio
    n_out = max(1, int(round(audio.size * dst / src)))
    return np.interp(
        np.linspace(0.0, 1.0, n_out, endpoint=False),
        np.linspace(0.0, 1.0, audio.size, endpoint=False),
        audio,
    ).astype(np.float32)


def synthesize(cfg: AssistantConfig, text: str) -> tuple[int, np.ndarray]:
    """Sintetiza texto según el proveedor configurado."""
    if cfg.tts_provider == "elevenlabs" and cfg.elevenlabs_api_key:
        return _elevenlabs(cfg, text)
    if cfg.tts_provider == "azure" and cfg.azure_key:
        return _azure(cfg, text)
    return _piper(cfg, text)


def _robotic_enabled() -> bool:
    """Lee el flag compartido `elevenlabs_robotic` del JSON de config."""
    try:
        import json as _json
        from reachy_mini_assistant.config import CONFIG_PATH

        data = _json.loads(open(CONFIG_PATH, encoding="utf-8").read())
        return bool(data.get("elevenlabs_robotic"))
    except Exception:  # noqa: BLE001
        return False


def _elevenlabs_voice_settings() -> dict:
    """Voice settings de ElevenLabs según el flag compartido `elevenlabs_robotic`.

    Se lee del JSON en cada síntesis para aplicar cambios al momento
    (sin reiniciar el asistente).
    """
    if _robotic_enabled():
        # Robot metálico pausada: estabilidad alta, un toque de humanidad,
        # sin boost de naturalidad y más lenta (orgánica, no apresurada).
        return {
            "stability": 1.0,
            "similarity_boost": 0.15,
            "style": 0.0,
            "use_speaker_boost": False,
            "speed": 0.85,
        }
    # Natural (por defecto): expresiva y cálida.
    return {
        "stability": 0.35,
        "similarity_boost": 0.8,
        "style": 0.55,
        "use_speaker_boost": True,
    }


def _robotize_pcm16(data: bytes) -> bytes:
    """Efecto robot de ciencia ficción sobre PCM 16-bit mono @16 kHz.

    - Ring modulation (~83 Hz): el clásico tono metálico (tipo Dalek/Sci-Fi).
    - Comb filter de 5 ms con realimentación: resonancia metálica.
    - Bitcrush a 6 bits: grano digital "de robot".
    La voz conserva la dicción de LIZ_PRUEBA_3 pero suena sintética.
    """
    if not data:
        return data
    import numpy as np

    x = np.frombuffer(data, dtype="<i2").astype(np.float32)
    n = x.size
    if n == 0:
        return data
    # 1) Ring modulation: metálico pero con profundidad moderada (menos distorsión).
    t = np.arange(n, dtype=np.float32) / 16000.0
    x = x * (0.4 + 0.6 * np.sin(2.0 * np.pi * 83.0 * t))
    # 2) Comb filter (retardo 5 ms realimentado): resonancia suave.
    d = 80  # 5 ms @ 16 kHz
    if n > 2 * d:
        y = x.copy()
        y[d:] += 0.45 * x[:-d]
        y[2 * d:] += 0.2 * x[:-2 * d]
        x = y
    # 3) Bitcrush a 8 bits: grano digital sutil (sin saturar).
    peak = float(np.max(np.abs(x))) or 1.0
    step = peak / 128.0
    x = np.round(x / step) * step
    # Recorte seguro sin distorsionar de más.
    peak = float(np.max(np.abs(x))) or 1.0
    if peak > 32000.0:
        x = x * (32000.0 / peak)
    return np.clip(x, -32768, 32767).astype(np.int16).tobytes()


def _elevenlabs(cfg: AssistantConfig, text: str) -> tuple[int, np.ndarray]:
    """Usa ElevenLabs (modelo flash multilingüe) en español, salida PCM 16 kHz.

    Usa un cliente HTTP persistente (keep-alive) para evitar el handshake TLS
    en cada respuesta (~200-400 ms de ahorro por turno).
    """
    import time

    voice_id = (cfg.elevenlabs_voice_id or "").strip() or "TX3LPaxmHKxFdv7VOQHJ"
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        "?output_format=pcm_16000"
    )
    headers = {
        "xi-api-key": cfg.elevenlabs_api_key,
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "model_id": cfg.elevenlabs_model or "eleven_flash_v2_5",
        "voice_settings": _elevenlabs_voice_settings(),
    }
    client = _elevenlabs_client()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            audio = resp.content
            if _robotic_enabled():
                audio = _robotize_pcm16(audio)
            pcm = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            return 16000, pcm
        except httpx.HTTPStatusError as e:
            last_error = e
            if e.response.status_code == 401 and attempt < 2:
                time.sleep(1.0)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last_error = e
            if attempt < 2:
                time.sleep(1.0)
                continue
            break
    raise RuntimeError(f"ElevenLabs falló tras reintentos: {last_error}")


_EL_CLIENT: httpx.Client | None = None


def _elevenlabs_client() -> httpx.Client:
    """Cliente HTTP persistente para ElevenLabs (reusa la conexión TLS)."""
    global _EL_CLIENT
    if _EL_CLIENT is None:
        _EL_CLIENT = httpx.Client(
            timeout=45.0,
            limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=60),
        )
    return _EL_CLIENT


def _piper(cfg: AssistantConfig, text: str) -> tuple[int, np.ndarray]:
    """Usa Piper (local). Descarga el modelo la primera vez."""
    model = _resolve_piper_model(cfg)
    piper_bin = os.path.join(os.path.dirname(sys.executable), "piper")
    if not os.path.exists(piper_bin):
        piper_bin = "piper"
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            [piper_bin, "-m", model, "-f", tmp],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
            check=False,
        )
        if not os.path.exists(tmp) or os.path.getsize(tmp) <= 44:
            raise RuntimeError("Piper no generó audio")
        rate, audio = _read_wav_pcm(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return OUT_RATE, _resample(audio, rate, OUT_RATE)


def _resolve_piper_model(cfg: AssistantConfig) -> str:
    """Resuelve el nombre de la voz a la ruta del modelo .onnx (descarga si falta)."""
    from pathlib import Path as _Path

    voice = cfg.piper_voice or "es_MX-ald-medium"
    p = _Path(voice)
    if p.suffix == ".onnx" and p.exists():
        return str(p)
    voices_dir = _Path.home() / ".local/share/piper_tts/voices"
    model = voices_dir / f"{voice}.onnx"
    if not model.exists():
        from piper.download_voices import download_voice  # type: ignore

        voices_dir.mkdir(parents=True, exist_ok=True)
        download_voice(voice, voices_dir)
    return str(model)


def _azure(cfg: AssistantConfig, text: str) -> tuple[int, np.ndarray]:
    import httpx

    url = f"https://{cfg.azure_region}.tts.speech.microsoft.com/cognitiveservices/v1"
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="es-CO">'
        f'<voice name="{cfg.azure_voice or "es-CO-SalomeNeural"}">{text}</voice></speak>'
    )
    headers = {
        "Ocp-Apim-Subscription-Key": cfg.azure_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers=headers, content=ssml.encode("utf-8"))
        resp.raise_for_status()
    audio = np.frombuffer(resp.content, dtype=np.int16).astype(np.float32) / 32768.0
    return 16000, audio

async def stream_audio(cfg, text, client=None):
    """Yield 50 ms PCM packets; preserve other providers and robotic processing."""
    if cfg.tts_provider != 'elevenlabs' or not cfg.elevenlabs_api_key or _robotic_enabled():
        rate, audio = await asyncio.to_thread(synthesize, cfg, text)
        audio = _resample(audio, rate, OUT_RATE)
        for start in range(0, len(audio), 800):
            yield audio[start:start+800]
        return
    if client is None:
        async with httpx.AsyncClient(timeout=45) as owned:
            async for audio in stream_audio(cfg, text, owned):
                yield audio
        return
    voice = (cfg.elevenlabs_voice_id or '').strip() or 'TX3LPaxmHKxFdv7VOQHJ'
    async with client.stream('POST',
                f'https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream',
                params={'output_format':'pcm_16000'},
                headers={'xi-api-key':cfg.elevenlabs_api_key},
                json={'text':text,'model_id':cfg.elevenlabs_model or 'eleven_flash_v2_5',
                      'voice_settings':_elevenlabs_voice_settings()}) as response:
        response.raise_for_status()
        pending=b''
        async for packet in response.aiter_bytes(1600):
            pending += packet
            size=len(pending)//2*2
            if size:
                yield np.frombuffer(pending[:size],dtype='<i2').astype(np.float32)/32768
                pending=pending[size:]
        if pending: raise RuntimeError('Audio PCM incompleto')
