"""STT y LLM vía Groq (rápido y gratuito)."""

from __future__ import annotations

import base64
import io
import json
import wave

import httpx

from reachy_mini_assistant.config import AssistantConfig

GROQ_URL = "https://api.groq.com/openai/v1"


def stt(cfg: AssistantConfig, audio: list[np.ndarray]) -> str:
    """Transcribe un segmento de audio (numpy float32 a 16 kHz) vía Groq."""
    import numpy as np

    samples = np.concatenate(audio) if audio else np.array([], dtype=np.float32)
    if samples.size == 0:
        return ""
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    buf.seek(0)

    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{GROQ_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {cfg.groq_api_key}"},
            data={"model": cfg.groq_stt_model, "language": cfg.language, "response_format": "json"},
            files={"file": ("voice.wav", buf.read(), "audio/wav")},
        )
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()


def llm_chat(
    cfg: AssistantConfig,
    messages: list[dict],
    tools: list[dict],
) -> dict:
    """Llama a Groq con function-calling. Devuelve {text, tool_name, tool_args}."""
    with httpx.Client(timeout=90) as client:
        resp = client.post(
            f"{GROQ_URL}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.groq_api_key}"},
            json={
                "model": cfg.groq_llm_model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        choice = resp.json()["choices"][0]["message"]

    if choice.get("tool_calls"):
        tc = choice["tool_calls"][0]
        fn = tc.get("function", {})
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        return {"text": "", "tool_name": name, "tool_args": args}

    return {"text": choice.get("content") or "", "tool_name": None, "tool_args": None}
