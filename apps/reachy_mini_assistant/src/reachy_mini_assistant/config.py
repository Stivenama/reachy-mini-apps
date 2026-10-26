"""Configuración compartida del asistente (personalidad, voz, proveedor TTS)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.getenv("ASSISTANT_CONFIG", "/home/pollen/reachy_voice_config.json"))


@dataclass
class AssistantConfig:
    personality: str = "Eres un asistente amable y cercano que ayuda a usar el robot."
    tts_provider: str = "elevenlabs"  # elevenlabs | piper | azure | xtts | cloud
    piper_voice: str = "es_MX-ald-medium"
    azure_voice: str = "es-CO-SalomeNeural"
    azure_key: str = ""
    azure_region: str = "eastus"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "TX3LPaxmHKxFdv7VOQHJ"
    elevenlabs_model: str = "eleven_flash_v2_5"
    elevenlabs_robotic: bool = False  # True = voz robotizada (monótona)
    hf_session_url: str = "https://pollen-robotics-reachy-mini-realtime-url.hf.space/session"
    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_llm_model: str = "qwen/qwen3.8-27b"
    language: str = "es"
    greet_on_start: bool = True
    greeting: str = "Hola, soy tu asistente del robot. Dime qué quieres hacer."
    volume: float = 1.0
    extra: dict = field(default_factory=dict)


def default_config() -> AssistantConfig:
    return AssistantConfig()


def load_config() -> AssistantConfig:
    cfg = default_config()
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for key in cfg.__dataclass_fields__:
                if key in data:
                    setattr(cfg, key, data[key])
            cfg.extra = {k: v for k, v in data.items() if k not in cfg.__dataclass_fields__}
    except Exception:
        pass
    return cfg


def save_config(cfg: AssistantConfig) -> None:
    data = {k: getattr(cfg, k) for k in cfg.__dataclass_fields__ if k != "extra"}
    data.update(cfg.extra)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def to_dict(cfg: AssistantConfig) -> dict:
    return {k: getattr(cfg, k) for k in cfg.__dataclass_fields__ if k != "extra"} | cfg.extra
