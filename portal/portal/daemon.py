"""Mi Reachy Portal - daemon (FastAPI).

Lista y abre todas las apps del robot (las de Pollen y las propias) desde una
única página web accesible en cualquier dispositivo de la red.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "portal_config.json"
PORT = int(os.getenv("MI_PORTAL_PORT", "8090"))

DAEMON_API = os.getenv("MI_PORTAL_DAEMON_URL", "http://127.0.0.1:8000")

app = FastAPI(title="Mi Reachy Portal")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- Utilidades ----------

def _wifi(path: str, method: str = "GET", params: dict | None = None, timeout: float = 60.0) -> dict:
    """Llama al router WiFi del daemon (raíz /wifi, no /api)."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.request(method, f"{DAEMON_API}/wifi/{path}", params=params)
            if resp.status_code >= 400:
                return {"ok": False, "status": resp.status_code, "detail": resp.text[:300]}
            try:
                return {"ok": True, "data": resp.json()}
            except Exception:
                return {"ok": True, "data": {"raw": resp.text[:300]}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _nmcli_scan() -> list[dict]:
    """Escaneo de redes WiFi vía nmcli (SSID, señal, seguridad)."""
    import subprocess
    import time

    networks: dict[str, dict] = {}
    # El rescan requiere autorización; con sudo (pollen tiene sudo sin contraseña).
    try:
        subprocess.run(["sudo", "nmcli", "dev", "wifi", "rescan"], timeout=20, capture_output=True)
        time.sleep(8)
    except Exception:  # noqa: BLE001
        pass
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
            timeout=20,
            capture_output=True,
            text=True,
        )
    except Exception as e:  # noqa: BLE001
        return [{"error": str(e)}]

    for raw in result.stdout.splitlines():
        fields = _split_nmcli_terse(raw)
        if len(fields) < 2:
            continue
        ssid = fields[0].strip()
        signal = fields[1].strip()
        security = fields[2].strip() if len(fields) > 2 else ""
        if not ssid:
            ssid = "(red oculta)"
        try:
            sig = int(signal) if signal.isdigit() else 0
        except Exception:
            sig = 0
        # Conservar la mejor señal por SSID
        if ssid not in networks or sig > networks[ssid]["signal"]:
            networks[ssid] = {"ssid": ssid, "signal": sig, "security": security}
    return sorted(networks.values(), key=lambda n: n["signal"], reverse=True)


def _split_nmcli_terse(line: str) -> list[str]:
    """Divide una línea terse de nmcli respetando '\\:'."""
    parts: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


def _daemon(path: str, method: str = "GET", timeout: float = 60.0) -> dict:
    """Llama al API del daemon de Reachy."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.request(method, f"{DAEMON_API}/api/{path}")
            if resp.status_code >= 400:
                return {"ok": False, "status": resp.status_code, "detail": resp.text[:300]}
            try:
                return {"ok": True, "data": resp.json()}
            except Exception:
                return {"ok": True, "data": {"raw": resp.text[:300]}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _load_config() -> list[dict]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data.get("apps", [])
    except Exception:
        return []


def _is_up(url: str) -> bool:
    try:
        with httpx.Client(timeout=1.0, follow_redirects=True) as client:
            resp = client.get(url)
            return resp.status_code < 500
    except Exception:
        return False


async def _probe_all(urls: list[str]) -> dict[str, bool]:
    """Comprueba todas las URLs en paralelo (rápido)."""
    import asyncio

    results: dict[str, bool] = {}

    async def _one(url: str) -> None:
        results[url] = await asyncio.to_thread(_is_up, url)

    await asyncio.gather(*(_one(u) for u in urls if u))
    return results


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"), headers={"Cache-Control": "no-store"})


@app.get("/api/apps")
async def api_apps(request: Request) -> JSONResponse:
    """Devuelve las apps registradas con su estado y URL accesible desde la red.

    La URL se reescribe usando el host que usó el cliente (la IP LAN del robot),
    así el móvil abre la app directamente sin necesidad de conocer puertos.
    """
    host_header = request.headers.get("host", "")
    hostname = host_header.split(":")[0] or "localhost"

    entries = _load_config()
    raw_urls = {str(e.get("url") or "").strip() for e in entries}
    online_map = await _probe_all(list(raw_urls))

    apps = []
    for entry in entries:
        raw_url = str(entry.get("url") or "").strip()
        url = raw_url
        if raw_url:
            parsed = urlparse(raw_url)
            scheme = parsed.scheme or "http"
            port = parsed.port or (443 if scheme == "https" else 80)
            path = parsed.path or "/"
            url = f"{scheme}://{hostname}:{port}{path}"

        apps.append(
            {
                "slug": entry.get("slug"),
                "nombre": entry.get("nombre"),
                "tipo": entry.get("tipo"),
                "icono": entry.get("icono", ""),
                "app_name": entry.get("app_name", ""),
                "url": url,
                "online": online_map.get(raw_url, False),
            }
        )
    return JSONResponse({"apps": apps, "total": len(apps)})


@app.post("/api/apps/{slug}/start")
def start_app(slug: str) -> JSONResponse:
    """Inicia una app en el daemon (auto-recupera si el estado queda pegado)."""
    entry = next((a for a in _load_config() if a.get("slug") == slug), None)
    if not entry:
        return JSONResponse({"ok": False, "error": "app desconocida"})
    app_name = str(entry.get("app_name") or "").strip()
    if not app_name:
        return JSONResponse({"ok": False, "error": "la app no tiene app_name configurado"})

    _daemon("apps/stop-current-app", "POST", timeout=60)
    stopped, last_state = _wait_app_stopped(timeout=30)

    if not stopped:
        # El daemon quedó pegado en 'stopping' (bug de Reachy): recuperar
        # reiniciándolo automáticamente para no dejar el sistema inutilizado.
        recovered = _restart_daemon_and_wait()
        if not recovered.get("ok"):
            return JSONResponse(
                {"ok": False, "error": recovered.get("error", "estado pegado"),
                 "state": last_state}
            )

    start = _daemon(f"apps/start-app/{app_name}", "POST", timeout=90)
    if not start.get("ok"):
        return JSONResponse({"ok": False, "error": "no se pudo arrancar la app", "start": start})

    running, final_state = _wait_app_running(app_name, timeout=120)
    raw_url = str(entry.get("url") or "").strip()
    _wait_app_online(raw_url, timeout=45)
    return JSONResponse({"ok": running, "state": final_state, "start": start})


@app.post("/api/apps/{slug}/stop")
def stop_app(slug: str) -> JSONResponse:
    """Detiene la app en curso; si queda pegada, reinicia el daemon."""
    _daemon("apps/stop-current-app", "POST", timeout=60)
    stopped, last_state = _wait_app_stopped(timeout=30)
    if stopped:
        return JSONResponse({"ok": True, "state": last_state})
    recovered = _restart_daemon_and_wait()
    return JSONResponse({"ok": recovered.get("ok", False), "state": last_state, "recover": recovered})


@app.post("/api/daemon/restart")
def restart_daemon() -> JSONResponse:
    """Reinicia el daemon de Reachy (por si se queda atascado)."""
    result = _restart_daemon_and_wait()
    return JSONResponse({"ok": result.get("ok", False), "message": "daemon reiniciado" if result.get("ok") else result.get("error")})


def _restart_daemon_and_wait(timeout: float = 75.0) -> dict:
    """Reinicia el daemon de Reachy y espera a que responda."""
    import subprocess
    import time

    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "reachy-mini-daemon"],
            timeout=60,
            capture_output=True,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_up(f"{DAEMON_API}/api/apps/current-app-status"):
            return {"ok": True}
        time.sleep(2)
    return {"ok": False, "error": "el daemon no respondió tras el reinicio"}


# ---------- Red WiFi ----------

@app.get("/api/wifi/status")
def wifi_status() -> JSONResponse:
    return JSONResponse(_wifi("status"))


@app.get("/api/wifi/error")
def wifi_error() -> JSONResponse:
    return JSONResponse(_wifi("error"))


@app.post("/api/wifi/scan")
def wifi_scan() -> JSONResponse:
    """Escaneo profesional (nmcli): SSID, señal y seguridad."""
    nets = _nmcli_scan()
    return JSONResponse({"ok": True, "networks": nets})


@app.post("/api/wifi/connect")
async def wifi_connect(request: Request) -> JSONResponse:
    """Conecta a una red (nueva o guardada) y espera el resultado.

    El daemon conecta en segundo plano; aquí esperamos hasta que la red pedida
    quede como conectada, o que el robot caiga al hotspot (fallo), o timeout.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    ssid = str(body.get("ssid") or "").strip()
    password = str(body.get("password") or "")
    if not ssid:
        return JSONResponse({"ok": False, "error": "ssid requerido"})

    res = _wifi("connect", "POST", params={"ssid": ssid, "password": password})
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("detail") or res.get("error")})

    connected, state = _wait_wifi_result(ssid, timeout=45)
    return JSONResponse(
        {
            "ok": True,
            "connected": connected,
            "network": (state or {}).get("connected_network", ""),
            "mode": (state or {}).get("mode", ""),
            "fallback_hotspot": not connected,
        }
    )


def _wait_wifi_result(ssid: str, timeout: float = 45) -> tuple[bool, dict | None]:
    """Espera a que el robot quede conectado a ``ssid`` o caiga al hotspot."""
    import time

    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        st = _wifi("status", timeout=10)
        data = st.get("data") or {}
        last = data
        connected = (data.get("connected_network") or "").strip()
        mode = (data.get("mode") or "").lower()
        if connected == ssid:
            return True, data
        if mode == "hotspot" or connected in ("reachy-mini-ap", "Hotspot"):
            return False, data
        time.sleep(2)
    return False, last


@app.post("/api/wifi/forget")
async def wifi_forget(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    ssid = str(body.get("ssid") or "").strip()
    if not ssid:
        return JSONResponse({"ok": False, "error": "ssid requerido"})
    return JSONResponse(_wifi("forget", "POST", params={"ssid": ssid}))


@app.post("/api/wifi/hotspot")
def wifi_hotspot() -> JSONResponse:
    return JSONResponse(_wifi("setup_hotspot", "POST"))


def _current_app() -> dict | None:
    """Estado actual de la app en el daemon (None si no hay app)."""
    result = _daemon("apps/current-app-status", "GET", timeout=15)
    if not result.get("ok"):
        return None
    data = result.get("data")
    return data if isinstance(data, dict) else None


def _wait_app_stopped(timeout: float = 45) -> tuple[bool, dict | None]:
    """Espera a que el daemon quede sin app en curso (estado idle/null)."""
    import time

    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        cur = _current_app()
        last = cur
        if cur is None:
            return True, cur
        info = cur.get("info") or {}
        if not info.get("name"):
            return True, cur
        state = (cur.get("state") or "").lower()
        if state in ("idle", "stopped", "none", "not_running"):
            return True, cur
        time.sleep(1)
    return False, last


def _wait_app_running(app_name: str, timeout: float = 120) -> tuple[bool, dict | None]:
    """Espera a que la app indicada quede en estado 'running'."""
    import time

    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        cur = _current_app()
        last = cur
        if cur is None:
            time.sleep(1)
            continue
        info = cur.get("info") or {}
        name = info.get("name") or ""
        state = (cur.get("state") or "").lower()
        if name == app_name and state == "running":
            return True, cur
        time.sleep(2)
    return False, last


def _wait_app_online(url: str, timeout: float = 45) -> bool:
    """Espera a que la app responda en su URL (abre su interfaz web)."""
    import time

    if not url:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_up(url):
            return True
        time.sleep(2)
    return False


def _ensure_hotspot_if_offline() -> None:
    """Si el robot no tiene una red WiFi conectada, activa su hotspot.

    El perfil 'Hotspot' de NetworkManager tiene autoconnect=no, así que sin esto
    el robot quedaría inalcanzable al moverlo a un lugar sin red conocida.
    """
    import subprocess
    import time

    time.sleep(15)  # dejar que NetworkManager se asiente al arrancar
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,STATE", "con", "show", "--active"],
            timeout=15,
            capture_output=True,
            text=True,
        )
        names = []
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] == "802-11-wireless" and parts[2] == "activated":
                names.append(parts[0])
        if any(n != "Hotspot" for n in names):
            return  # ya hay una red real conectada
        if "Hotspot" in names:
            return  # el hotspot ya está activo
        subprocess.run(["sudo", "nmcli", "con", "up", "Hotspot"], timeout=40, capture_output=True)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    import threading
    import uvicorn

    threading.Thread(target=_ensure_hotspot_if_offline, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()