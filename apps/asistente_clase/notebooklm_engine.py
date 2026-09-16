"""Envoltura del CLI de notebooklm-py. Capa no oficial y aislada.

Todo el acceso a NotebookLM pasa por aquí. Si Google cambia algo, solo hay que
ajustar este módulo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tempfile
from pathlib import Path

CLI = Path(sys.executable).parent / ("notebooklm.exe" if os.name == "nt" else "notebooklm")

_current = None
_current_lock = threading.Lock()


def _run(args: list[str], timeout: int = 600) -> str:
    global _current
    if not CLI.exists():
        raise RuntimeError(f"No encontré el CLI de notebooklm en {CLI}")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        [str(CLI), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    with _current_lock:
        _current = process
    try:
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
            raise RuntimeError("El comando de NotebookLM excedió el tiempo límite")
    finally:
        with _current_lock:
            _current = None
    if process.returncode != 0:
        detail = (err or out or "").strip()
        lines = [line for line in detail.splitlines() if line.strip()]
        detail = lines[-1] if lines else detail
        if "GET_NOTEBOOK" in detail or "rpc_code=5" in detail:
            raise RuntimeError("El notebook no existe o no tienes acceso a él (fue eliminado).")
        if "Authentication expired" in detail or "notebooklm login" in detail:
            raise RuntimeError("La sesión de Gemini Notebook caducó. Pulsa Reconectar.")
        raise RuntimeError(detail[-600:] or "El comando de NotebookLM falló")
    return out


def kill_running() -> None:
    """Detiene el comando de NotebookLM que esté en curso (cancelación)."""
    with _current_lock:
        process = _current
    if process and process.poll() is None:
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass


def _json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise RuntimeError("Respuesta inesperada de NotebookLM: " + text.strip()[:200])
    return json.loads(text[start : end + 1])


def list_notebooks(profile: str) -> list[dict]:
    return _json(_run(["-p", profile, "list", "--json"]))["notebooks"]


def create_notebook(profile: str, title: str) -> str:
    data = _json(_run(["-p", profile, "create", title, "--json"]))
    return data["notebook"]["id"]


def find_or_create_notebook(profile: str, title: str) -> str:
    wanted = (title or "").strip().lower()
    for item in list_notebooks(profile):
        if (item.get("title") or "").strip().lower() == wanted:
            return item["id"]
    return create_notebook(profile, title)


def notebook_exists(profile: str, notebook_id: str) -> bool:
    if not notebook_id:
        return False
    try:
        return any((item.get("id") == notebook_id) for item in list_notebooks(profile))
    except Exception:  # noqa: BLE001 - si falla la consulta no forzamos recreación
        return True


def add_source(profile: str, notebook_id: str, path: str) -> str:
    data = _json(
        _run(
            ["-p", profile, "source", "add", path, "--type", "file", "-n", notebook_id, "--json"],
            timeout=900,
        )
    )
    return data["source"]["id"]


def add_url_source(profile: str, notebook_id: str, url: str) -> str:
    data = _json(
        _run(
            ["-p", profile, "source", "add", url, "-n", notebook_id, "--json"],
            timeout=900,
        )
    )
    return data["source"]["id"]


def research_discover(profile: str, notebook_id: str, query: str, mode: str = "default") -> list[dict]:
    """Investiga fuentes web sobre un tema y devuelve la lista clasificada."""
    data = _json(
        _run(
            ["-p", profile, "research", "discover", query, "--mode", mode, "-n", notebook_id, "--json"],
            timeout=300,
        )
    )
    return data.get("sources", []) or []


def ask_notebook(profile: str, notebook_id: str, question: str, source_id: str | None = None) -> str:
    """Pregunta al notebook (chat anclado a las fuentes) y devuelve la respuesta."""
    args = ["-p", profile, "ask", "--new", "--json", "-n", notebook_id]
    if source_id:
        args += ["-s", source_id]
    # Long research candidate lists exceed Windows command-line limits.
    with tempfile.TemporaryDirectory(prefix="reachy-prompt-") as folder:
        prompt = Path(folder) / "prompt.txt"
        prompt.write_text(question, encoding="utf-8")
        data = _json(_run([*args, "--prompt-file", str(prompt)], timeout=300))
    return str(data.get("answer") or "").strip()


def generate(profile: str, notebook_id: str, kind: str, language: str, timeout: int) -> dict:
    tail = ["-n", notebook_id, "--json", "--wait", "--timeout", str(timeout)]
    if kind == "report":
        args = ["generate", "report", "--format", "briefing-doc", "--language", language, *tail]
    elif kind == "infographic":
        args = ["generate", "infographic", "--language", language, "--orientation", "portrait", *tail]
    elif kind == "video":
        args = ["generate", "video",
                "Presentación académica sobria de nivel universitario, basada principalmente en "
                "los conceptos y explicaciones de la transcripción. Distingue lo dicho en clase "
                "de las aportaciones de fuentes complementarias. Explica definiciones, fundamentos, "
                "ejemplos y límites; identifica las fuentes cuando estén disponibles. No inventes "
                "referencias ni conviertas hipótesis en hechos. Evita sensacionalismo, humor, "
                "infantilización y afirmaciones sin respaldo. Usa diagramas claros y tono docente.",
                "--language", language, "--style", "classic", "--format", "explainer", *tail]
    else:
        raise ValueError(f"artefacto desconocido: {kind}")
    return _json(_run(["-p", profile, *args], timeout=timeout + 120))


def download(profile: str, notebook_id: str, kind: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        ["-p", profile, "download", kind, str(output_path), "-n", notebook_id, "--force"],
        timeout=1800,
    )
