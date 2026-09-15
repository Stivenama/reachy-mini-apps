"""Configuración de la app: perfiles, rutas y opciones."""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
SECRETS = BASE / "secrets"

DEFAULTS = {
    "profiles": {
        "personal": {
            "label": "Personal",
            "notebooklm_profile": "default",
            "google_token": "secrets/token.json",
        }
    },
    "active_profile": "personal",
    "transcripts_dir": str(BASE / "transcripciones"),
    "client_secret": "secrets/client_secret.json",
    "drive_root": "Reachy Classes",
    "default_topic": "Material de clases",
    "language": "es",
    "port": 8099,
    "report_timeout": 900,
    "infographic_timeout": 900,
    "video_timeout": 2400,
}


def load() -> dict:
    if not CONFIG_PATH.exists():
        save(DEFAULTS)
        return json.loads(json.dumps(DEFAULTS))
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for key, value in DEFAULTS.items():
        data.setdefault(key, value)
    return data


def save(config: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def get_profile(config: dict, profile_id: str | None) -> dict:
    profiles = config["profiles"]
    if profile_id and profile_id in profiles:
        return {"id": profile_id, **profiles[profile_id]}
    active = config.get("active_profile") or next(iter(profiles))
    config["active_profile"] = active
    return {"id": active, **profiles[active]}


def resolve(config: dict, relative: str) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else (BASE / path)


def client_secret_path(config: dict) -> Path:
    return resolve(config, config["client_secret"])


def token_path(config: dict, profile: dict) -> Path:
    return resolve(config, profile["google_token"])
