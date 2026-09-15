"""Agrega una cuenta (perfil) nueva a la app.

Hace dos cosas, guiadas:
1. Login de NotebookLM (abre el navegador).
2. Autorización de Google Drive + Classroom (abre el navegador).

Antes de usarlo, agrega el correo como "usuario de prueba" en la pantalla
de consentimiento OAuth de Google Cloud.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import settings as S
from google_auth import CLIENT_SECRET, get_credentials
from notebooklm_engine import CLI


def slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "cuenta"


def main() -> None:
    config = S.load()
    label = input("Nombre de la cuenta (ej. UNAL Profesor): ").strip() or "Cuenta"
    profile_id = slug(label)
    if profile_id in config["profiles"]:
        print(f"Ya existe un perfil '{profile_id}'.")
        return

    notebooklm_profile = profile_id
    token_file = S.SECRETS / f"token_{profile_id}.json"

    print(f"\n1) Login de NotebookLM para el perfil '{notebooklm_profile}'.")
    print("   Se abrirá el navegador. Inicia sesión con la cuenta de esta perfil.")
    input("   Pulsa Enter para continuar…")
    import subprocess

    result = subprocess.run([str(CLI), "-p", notebooklm_profile, "login"])
    if result.returncode != 0:
        print("El login de NotebookLM no terminó correctamente. Se cancela.")
        return

    print(f"\n2) Autorización de Google (Drive + Classroom).")
    print("   Se abrirá el navegador. Elige la MISMA cuenta y acepta los permisos.")
    if not CLIENT_SECRET.exists():
        print(f"   Falta {CLIENT_SECRET}.")
        return
    input("   Pulsa Enter para continuar…")
    get_credentials(token_file, CLIENT_SECRET, interactive=True)

    config["profiles"][profile_id] = {
        "label": label,
        "notebooklm_profile": notebooklm_profile,
        "google_token": str(token_file.relative_to(S.BASE)).replace("\\", "/"),
    }
    S.save(config)
    print(f"\nCuenta '{label}' agregada. Perfil activo: {config.get('active_profile')}.")
    print("Reinicia la app para que aparezca en el selector de Cuenta.")


if __name__ == "__main__":
    sys.exit(main())
