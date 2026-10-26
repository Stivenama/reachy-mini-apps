"""Herramientas que el asistente puede invocar (control del portal y del robot)."""

from __future__ import annotations

import httpx

PORTAL = "http://127.0.0.1:8090"
DAEMON = "http://127.0.0.1:8000"

# Nombre amigable -> slug del portal (las apps registradas en el portal).
APP_SLUGS = {
    "hub": "hub",
    "reachy hub": "hub",
    "audio mixer": "audio_mixer",
    "mezclador": "audio_mixer",
    "clase": "classroom",
    "clase continua": "classroom",
    "classroom": "classroom",
    "asistente": "asistente",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "abrir_app",
            "description": "Abre una aplicación del robot desde el portal. Cierra la aplicación actual.",
            "parameters": {
                "type": "object",
                "properties": {"nombre": {"type": "string", "description": "App: hub, audio mixer, clase, asistente"}},
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cerrar_app",
            "description": "Cierra la aplicación en curso y vuelve el asistente.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mover_robot",
            "description": "Mueve el robot: despertar, dormir o detener.",
            "parameters": {
                "type": "object",
                "properties": {"accion": {"type": "string", "enum": ["despertar", "dormir", "detener"]}},
                "required": ["accion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cambiar_red",
            "description": "Conecta el robot a otra red WiFi.",
            "parameters": {
                "type": "object",
                "properties": {"ssid": {"type": "string"}, "password": {"type": "string"}},
                "required": ["ssid", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reiniciar_daemon",
            "description": "Reinicia el daemon de Reachy (por si quedó atascado).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estado_robot",
            "description": "Consulta el estado actual: qué app está abierta y la red conectada.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _post(base: str, path: str, timeout: float = 60) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{base}{path}")
            if resp.status_code >= 400:
                return {"ok": False, "detail": resp.text[:200]}
            try:
                return {"ok": True, "data": resp.json()}
            except Exception:
                return {"ok": True, "data": {"raw": resp.text[:200]}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}


def _get(base: str, path: str, timeout: float = 15) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base}{path}")
            if resp.status_code >= 400:
                return {"ok": False, "detail": resp.text[:200]}
            try:
                return {"ok": True, "data": resp.json()}
            except Exception:
                return {"ok": True, "data": {"raw": resp.text[:200]}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}


def execute(name: str, args: dict, notify: "callable | None" = None) -> dict:
    """Ejecuta una herramienta. Devuelve {result_text, stop_assistant}.

    ``notify(texto)`` es un callback hilo-seguro para narrar resultados
    que ocurren en segundo plano (p. ej. si abrir la app falla tarde).
    """
    import threading

    if name == "abrir_app":
        nombre = str(args.get("nombre") or "").lower()
        slug = APP_SLUGS.get(nombre)
        if not slug:
            return {"result_text": f"No conozco la app '{nombre}'. Las apps son: hub, audio mixer, clase.", "stop_assistant": False}
        # Abrir la app en segundo portal: no bloquea el bucle del motor.
        def _bg_start() -> None:
            res = _post(PORTAL, f"/api/apps/{slug}/start", timeout=150)
            if not res.get("ok") and notify is not None:
                try:
                    notify(f"No pude abrir {slug}: {res.get('detail', 'error')}.")
                except Exception:
                    pass

        threading.Thread(target=_bg_start, daemon=True).start()
        return {"result_text": f"Abriendo {slug}.", "stop_assistant": True}

    if name == "cerrar_app":
        def _bg_stop() -> None:
            res = _post(PORTAL, "/api/apps/hub/stop", timeout=60)
            if not res.get("ok") and notify is not None:
                try:
                    notify(f"No pude cerrar la aplicación: {res.get('detail', 'error')}.")
                except Exception:
                    pass

        threading.Thread(target=_bg_stop, daemon=True).start()
        return {"result_text": "Cerrando la aplicación.", "stop_assistant": True}

    if name == "reiniciar_daemon":
        res = _post(PORTAL, "/api/daemon/restart", timeout=90)
        return {"result_text": "Reinicié el daemon de Reachy." if res.get("ok") else f"No pude: {res.get('detail', 'error')}", "stop_assistant": False}

    if name == "mover_robot":
        accion = str(args.get("accion") or "").lower()
        path = {"despertar": "/api/move/play/wake_up", "dormir": "/api/move/play/goto_sleep", "detener": "/api/move/stop"}.get(accion)
        if not path:
            return {"result_text": "Acción de movimiento desconocida.", "stop_assistant": False}
        res = _post(DAEMON, path)
        return {"result_text": f"Movimiento '{accion}' solicitado." if res.get("ok") else f"No pude: {res.get('detail', 'error')}", "stop_assistant": False}

    if name == "cambiar_red":
        ssid = str(args.get("ssid") or "").strip()
        password = str(args.get("password") or "")
        if not ssid:
            return {"result_text": "Necesito el nombre de la red.", "stop_assistant": False}
        try:
            with httpx.Client(timeout=20) as client:
                resp = client.post(f"{DAEMON}/wifi/connect", params={"ssid": ssid, "password": password})
            ok = resp.status_code < 400
            return {"result_text": f"Conectando a {ssid}." if ok else f"No pude conectar: {resp.text[:120]}", "stop_assistant": False}
        except Exception as e:  # noqa: BLE001
            return {"result_text": f"No pude conectar: {e}", "stop_assistant": False}

    if name == "estado_robot":
        app = _get(DAEMON, "/api/apps/current-app-status")
        wifi = _get(DAEMON, "/wifi/status")
        app_name = (app.get("data") or {}).get("info", {}).get("name") if app.get("ok") else None
        net = (wifi.get("data") or {}).get("connected_network", "desconocida") if wifi.get("ok") else "desconocida"
        return {"result_text": f"App abierta: {app_name or 'ninguna'}. Red WiFi: {net}.", "stop_assistant": False}

    return {"result_text": f"Herramienta desconocida: {name}", "stop_assistant": False}
