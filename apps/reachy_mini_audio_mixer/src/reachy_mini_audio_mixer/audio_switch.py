"""Lógica de conmutación de salida de audio del Reachy Mini.

Las tarjetas ALSA se detectan por nombre en ``aplay -l`` (los índices cambian
tras reinicios), asi que no se hardcodean numeros de tarjeta.
"""

from __future__ import annotations

import json
import math
import re
import struct
import subprocess
import wave
from pathlib import Path

ASOUNDRC = Path.home() / ".asoundrc"
STATE_FILE = Path.home() / ".config" / "reachy_mini" / "audio_output.json"

DEVICES = [
    {
        "id": "internal",
        "label": "Parlante interno",
        "detail": "Reachy Mini Audio",
    },
    {
        "id": "external",
        "label": "USB-C externo",
        "detail": "AB13X USB Audio",
    },
]


def _detect_cards() -> dict[str, int]:
    """Detectar los numeros de tarjeta por nombre en aplay -l."""
    detected = {"internal": 0, "external": 3}
    try:
        result = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=5)
        for match in re.finditer(r"card (\d+):\s+(\S+)\s+\[([^\]]+)\]", result.stdout):
            card = int(match.group(1))
            name = match.group(3)
            if "Reachy Mini Audio" in name:
                detected["internal"] = card
            elif "AB13X" in name:
                detected["external"] = card
    except Exception:
        pass
    return detected


def _card_for(device_id: str) -> int:
    return _detect_cards().get(device_id, 0)


def _asoundrc_text(device_id: str) -> str:
    """Construir el ~/.asoundrc para el dispositivo pedido."""
    cards = _detect_cards()
    internal = cards["internal"]
    external = cards["external"]
    if device_id == "internal":
        return f"""pcm.!default {{
    type hw
    card {internal}
}}

ctl.!default {{
    type hw
    card {internal}
}}

pcm.reachymini_audio_sink {{
    type dmix
    ipc_key 4241
    slave {{
        pcm "hw:{internal},0"
        channels 2
        period_size 256
        buffer_size 1024
        rate 16000
    }}
    bindings {{
        0 0
        1 1
    }}
}}

pcm.reachymini_audio_src {{
    type dsnoop
    ipc_key 4242
    slave {{
        pcm "hw:{internal},0"
        channels 2
        rate 16000
        period_size 256
        buffer_size 1024
    }}
}}
"""
    return f"""pcm.!default {{
    type hw
    card {internal}
}}

ctl.!default {{
    type hw
    card {internal}
}}

pcm.reachymini_audio_sink {{
    type plug
    slave {{
        pcm "dmix3"
    }}
}}

pcm.dmix3 {{
    type dmix
    ipc_key 4243
    slave {{
        pcm "hw:{external},0"
        channels 2
    }}
}}

pcm.reachymini_audio_src {{
    type dsnoop
    ipc_key 4242
    slave {{
        pcm "hw:{internal},0"
        channels 2
        rate 16000
        period_size 256
        buffer_size 1024
    }}
}}
"""


def get_current_output() -> str:
    """Devolver el dispositivo de salida activo."""
    try:
        text = ASOUNDRC.read_text(encoding="utf-8")
    except OSError:
        return "external"
    return "external" if "dmix3" in text else "internal"


def set_audio_output(device_id: str) -> str:
    """Escribir el ~/.asoundrc para el dispositivo pedido."""
    if device_id not in {device["id"] for device in DEVICES}:
        raise ValueError(f"Dispositivo desconocido: {device_id}")
    ASOUNDRC.write_text(_asoundrc_text(device_id), encoding="utf-8")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"output": device_id}), encoding="utf-8")
    return device_id


def get_volume(device_id: str | None = None) -> int:
    """Volumen (0-100) del dispositivo indicado (o el actual)."""
    device_id = device_id or get_current_output()
    card = _card_for(device_id)
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "get", "PCM"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return -1
    for line in result.stdout.splitlines():
        if "[" in line and "%]" in line:
            try:
                return int(line.split("[")[1].split("%]")[0])
            except (IndexError, ValueError):
                continue
    return -1


def set_volume(volume: int, device_id: str | None = None) -> int:
    """Fijar el volumen (0-100) del dispositivo indicado (o el actual)."""
    volume = max(0, min(100, int(volume)))
    device_id = device_id or get_current_output()
    card = _card_for(device_id)
    subprocess.run(
        ["amixer", "-c", str(card), "set", "PCM", f"{volume}%"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return volume


def play_test_sound() -> str:
    """Reproducir un tono corto por la salida actual."""
    wav_path = Path("/tmp/mixer_beep.wav")
    with wave.open(str(wav_path), "w") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(16000)
        frames = bytearray()
        for i in range(16000):
            s = struct.pack("<h", int(7000 * math.sin(2 * math.pi * 660 * i / 16000)))
            frames += s + s
        f.writeframes(bytes(frames))
    subprocess.run(
        ["aplay", "-D", "reachymini_audio_sink", str(wav_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return "ok"