"""Alta de cuentas (perfiles) desde la interfaz, con progreso en vivo."""

from __future__ import annotations

import re
import subprocess
import threading
import time

import notebooklm_engine as nb
import settings as S
from google_auth import get_credentials

_lock = threading.Lock()
_status = {
    "running": False,
    "step": "idle",
    "message": "",
    "email": "",
    "profile_id": "",
    "error": "",
    "log": [],
}


def _log(message: str) -> None:
    _status["log"].append("%s  %s" % (time.strftime("%H:%M:%S"), message))
    _status["log"] = _status["log"][-100:]


def status() -> dict:
    return dict(_status)


def _slug(email: str) -> str:
    base = email.split("@", 1)[0].lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base or "cuenta"


def start(email: str) -> tuple[bool, str]:
    with _lock:
        if _status["running"]:
            return False, "Ya hay una cuenta agregándose. Espera a que termine."
        profile_id = _slug(email)
        config = S.load()
        if profile_id in config["profiles"]:
            return False, f"La cuenta {email} ya está agregada."
        _status.update(
            running=True, step="starting", message="Iniciando…", email=email,
            profile_id=profile_id, error="", log=[],
        )
    _log(f"Cuenta: {email} (perfil '{profile_id}')")
    threading.Thread(target=_flow, args=(email, profile_id), daemon=True).start()
    return True, profile_id


def relogin(profile_id: str) -> tuple[bool, str]:
    """Vuelve a iniciar sesión de Gemini Notebook para un perfil existente."""
    with _lock:
        if _status["running"]:
            return False, "Ya hay una operación de cuenta en curso."
        config = S.load()
        if profile_id not in config["profiles"]:
            return False, "Perfil desconocido."
        profile = config["profiles"][profile_id]
        nbp = profile.get("notebooklm_profile", profile_id)
        _status.update(
            running=True, step="notebooklm", message="Reconectando sesión de Gemini Notebook…",
            email=profile.get("label", profile_id), profile_id=profile_id, error="", log=[],
        )
    _log(f"Reconectando Gemini Notebook para el perfil '{nbp}'.")
    threading.Thread(target=_relogin_flow, args=(nbp,), daemon=True).start()
    return True, profile_id


def _relogin_flow(nbp: str) -> None:
    try:
        result = subprocess.run([str(nb.CLI), "-p", nbp, "login"])
        if result.returncode != 0:
            raise RuntimeError("No se completó el inicio de sesión.")
        _log("Sesión de Gemini Notebook reconectada.")
        _status.update(step="done", message="Sesión reconectada", running=False)
    except Exception as error:  # noqa: BLE001
        message = str(error)[:500]
        _log("ERROR: " + message)
        _status.update(step="error", message=message, running=False, error=message)


def _flow(email: str, profile_id: str) -> None:
    try:
        _status.update(step="notebooklm", message="Abriendo login de Gemini Notebook…")
        _log("Abriendo navegador para el login de NotebookLM.")
        result = subprocess.run([str(nb.CLI), "-p", profile_id, "login"])
        if result.returncode != 0:
            raise RuntimeError("El login de NotebookLM no se completó o se canceló.")
        _log("Login de NotebookLM correcto.")

        _status.update(step="google", message="Autoriza Google Drive + Classroom en el navegador…")
        _log("Abriendo el consentimiento de Google.")
        config = S.load()
        token_file = S.SECRETS / f"token_{profile_id}.json"
        get_credentials(token_file, S.client_secret_path(config), interactive=True)
        _log("Autorización de Google correcta.")

        config = S.load()
        config["profiles"][profile_id] = {
            "label": email,
            "notebooklm_profile": profile_id,
            "google_token": str(token_file.relative_to(S.BASE)).replace("\\", "/"),
        }
        config.setdefault("active_profile", profile_id)
        S.save(config)
        _log(f"Cuenta {email} agregada.")
        _status.update(step="done", message="Cuenta agregada", running=False)
    except Exception as error:  # noqa: BLE001
        message = str(error)[:500]
        _log("ERROR: " + message)
        _status.update(step="error", message=message, running=False, error=message)
