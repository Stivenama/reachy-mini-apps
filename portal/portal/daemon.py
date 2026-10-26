"""Mi Reachy Portal - daemon (FastAPI).

Lista y abre todas las apps del robot (las de Pollen y las propias) desde una
única página web accesible en cualquier dispositivo de la red.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
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

# Última app iniciada: el frontend la abre sola en una pestaña nueva.
_pending_open: dict = {"slug": None, "ts": 0}

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


def _daemon(path: str, method: str = "GET", timeout: float = 60.0, body: dict | None = None) -> dict:
    """Llama al API del daemon de Reachy."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            kwargs = {"json": body} if body is not None else {}
            resp = client.request(method, f"{DAEMON_API}/api/{path}", **kwargs)
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
    if running:
        # Señal para que el navegador del usuario abra la app solo.
        _pending_open.update({"slug": slug, "ts": int(__import__("time").time())})
    return JSONResponse({"ok": running, "state": final_state, "start": start})


PORTAL_VERSION = "22"


@app.get("/api/version")
def api_version() -> JSONResponse:
    """Versión del frontend: si el cliente corre una vieja, se recarga solo."""
    return JSONResponse({"v": PORTAL_VERSION})


@app.get("/api/pending-open")
def api_pending_open() -> JSONResponse:
    """La última app iniciada (válida 90 s) para que el frontend la abra sola."""
    import time

    slug = _pending_open.get("slug")
    ts = int(_pending_open.get("ts") or 0)
    if not slug or time.time() - ts > 90:
        return JSONResponse({"slug": None, "ts": 0})
    return JSONResponse({"slug": slug, "ts": ts})


# ---------- Movimientos (bailes y gestos desde Hugging Face) ----------

MOVIMIENTOS_DATASETS = {
    "bailes": "pollen-robotics/reachy-mini-dances-library",
    "emociones": "pollen-robotics/reachy-mini-emotions-library",
}
_MOV_CACHE: dict[str, tuple[float, list[str]]] = {}


def _mov_dataset(categoria: str) -> str:
    return MOVIMIENTOS_DATASETS.get(categoria, categoria)


def _mov_enable_motors() -> bool:
    """Reactiva los motores si están en disabled (los deja así goto_sleep).

    wake_up y los bailes comandan poses sobre los motores; si están apagados
    el daemon responde 200 pero el robot no se mueve.
    """
    try:
        st = _daemon("motors/status", "GET", timeout=8)
        data = st.get("data") if isinstance(st.get("data"), dict) else {}
        if data.get("mode") == "enabled":
            return True
        res = _daemon("motors/set_mode/enabled", "POST", timeout=10)
        time.sleep(0.4)
        return bool(res.get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _mov_running_uuids() -> list[str]:
    run = _daemon("move/running", "GET", timeout=8)
    data = run.get("data")
    items = data if isinstance(data, list) else []
    uuids: list[str] = []
    for it in items:
        if isinstance(it, dict) and it.get("uuid"):
            uuids.append(str(it["uuid"]))
    return uuids


def _mov_stop_all(timeout: float = 6.0) -> bool:
    """Cancela TODO movimiento en curso y espera a que el candado quede libre.

    POST /api/move/stop exige {"uuid": ...} (sin body devuelve 422); por eso
    se listan los uuids activos y se cancelan uno por uno. Mientras quede
    alguna tarea, el daemon RECHAZA cualquier play nuevo (try_start_move), así
    que dormir/despertar deben esperar a que no quede ninguna.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        uuids = _mov_running_uuids()
        if not uuids:
            return True
        for u in uuids:
            _daemon("move/stop", "POST", timeout=8, body={"uuid": u})
        time.sleep(0.25)
    return not _mov_running_uuids()


@app.get("/api/movimientos")
def api_movimientos(categoria: str = "bailes") -> JSONResponse:
    """Lista de movimientos de un dataset de Hugging Face (cacheada 10 min)."""
    import time as _time

    dataset = _mov_dataset(categoria)
    hit = _MOV_CACHE.get(dataset)
    if hit and _time.time() - hit[0] < 600:
        return JSONResponse(
            {"ok": True, "categoria": categoria, "dataset": dataset, "moves": hit[1]}
        )
    res = _daemon(f"move/recorded-move-datasets/list/{dataset}", "GET", timeout=90)
    if not res.get("ok"):
        return JSONResponse(
            {"ok": False, "error": res.get("detail") or res.get("error") or "sin datos"},
            status_code=502,
        )
    moves = res.get("data")
    if not isinstance(moves, list):
        return JSONResponse(
            {"ok": False, "error": "respuesta inesperada del robot"}, status_code=502
        )
    _MOV_CACHE[dataset] = (_time.time(), moves)
    return JSONResponse(
        {"ok": True, "categoria": categoria, "dataset": dataset, "moves": moves}
    )


@app.post("/api/movimientos/play")
async def api_movimientos_play(request: Request) -> JSONResponse:
    body = await request.json()
    dataset = _mov_dataset(str(body.get("categoria") or "bailes"))
    if body.get("dataset"):
        dataset = str(body["dataset"])
    move = str(body.get("move") or "").strip()
    if not move:
        return JSONResponse({"ok": False, "error": "falta el movimiento"}, status_code=400)
    # Un clic explícito gana: si hay un programa activo se detiene antes de
    # reproducir, para que no compitan por el candado del daemon.
    with _MOV_PROG_LOCK:
        _MOV_PROG["activo"] = False
    _mov_stop_all()
    _mov_enable_motors()
    res = _daemon(
        f"move/play/recorded-move-dataset/{dataset}/{move}", "POST", timeout=30
    )
    if not res.get("ok"):
        return JSONResponse(
            {"ok": False, "error": res.get("detail") or res.get("error") or "falló"},
            status_code=502,
        )
    return JSONResponse({"ok": True, "dataset": dataset, "move": move})


@app.post("/api/movimientos/stop")
def api_movimientos_stop() -> JSONResponse:
    return JSONResponse(_daemon("move/stop", "POST", timeout=15))


@app.get("/api/movimientos/running")
def api_movimientos_running() -> JSONResponse:
    return JSONResponse(_daemon("move/running", "GET", timeout=10))


@app.post("/api/movimientos/basico")
async def api_movimientos_basico(request: Request) -> JSONResponse:
    """Acciones básicas: wake_up / goto_sleep.

    Secuencia robusta (evita las carreras del segundo dormir/despertar):
    1. Detiene cualquier programa de bailes activo (si no, sigue inyectando
       movimientos alrededor del dormir/despertar y roba el candado).
    2. Cancela TODO movimiento en curso y espera a que el candado quede libre
       (si no, el daemon IGNORA el play nuevo y el dormir termina apagando
       los motores "después" de tu despertar).
    3. Para despertar: reactiva motores (goto_sleep los deja en disabled).
    4. Recién entonces ejecuta la acción.
    """
    body = await request.json()
    accion = str(body.get("accion") or "")
    if accion not in ("wake_up", "goto_sleep"):
        return JSONResponse({"ok": False, "error": "acción desconocida"}, status_code=400)
    with _MOV_PROG_LOCK:
        programa_activo = bool(_MOV_PROG.get("activo"))
        _MOV_PROG["activo"] = False
    libre = _mov_stop_all()
    if accion == "wake_up":
        _mov_enable_motors()
    res = _daemon(f"move/play/{accion}", "POST", timeout=30)
    return JSONResponse(
        {
            **res,
            "programa_detenido": programa_activo,
            "candado_libre": libre,
        }
    )


# ---------- Programa de bailes (duración configurable) ----------

_MOV_PROG: dict = {"activo": False, "hasta": 0.0, "total": 0.0, "modo": "", "move": ""}
_MOV_PROG_LOCK = threading.Lock()
_MOV_DUR: dict[str, float] = {}  # duración medida por "dataset/move" (para encadenar sin pausa)


def _mov_programa_estado() -> dict:
    with _MOV_PROG_LOCK:
        activo = bool(_MOV_PROG.get("activo"))
        hasta = float(_MOV_PROG.get("hasta") or 0.0)
        quedan = max(0.0, hasta - time.time()) if activo else 0.0
        return {
            "activo": activo,
            "quedan": round(quedan, 1),
            "total": _MOV_PROG.get("total"),
            "modo": _MOV_PROG.get("modo"),
            "move": _MOV_PROG.get("move"),
        }


def _mov_loop(fin: float, modo: str, dataset: str, move: str | None) -> None:
    """Repite movimientos (fijo o al azar) hasta `fin` o hasta que lo paran.

    Encadenado fluido: el daemon RECHAZA un play mientras otro corre
    (try_start_move), así que el siguiente play se lanza en cuanto el actual
    termina. El fin del movimiento se sondea a 10 Hz, y a 25 Hz en el tramo
    final cuando se conoce su duración medida, para que la pausa entre bailes
    sea de ~0.1 s en lugar de ~0.8 s.
    """
    natural = True
    try:
        while True:
            with _MOV_PROG_LOCK:
                if not _MOV_PROG.get("activo"):
                    natural = False
                    break
            if time.time() >= fin:
                break
            if modo == "fijo" and move:
                elegido = move
            else:
                hit = _MOV_CACHE.get(dataset)
                lista = hit[1] if hit else None
                if not lista:
                    res = _daemon(
                        f"move/recorded-move-datasets/list/{dataset}", "GET", timeout=90
                    )
                    if isinstance(res.get("data"), list) and res["data"]:
                        lista = res["data"]
                        _MOV_CACHE[dataset] = (time.time(), lista)
                if not lista:
                    break
                elegido = random.choice(lista)
            clave = f"{dataset}/{elegido}"
            with _MOV_PROG_LOCK:
                if not _MOV_PROG.get("activo"):
                    natural = False
                    break
            print(f"[portal] programa: play {elegido}", flush=True)
            res_play = _daemon(
                f"move/play/recorded-move-dataset/{dataset}/{elegido}",
                "POST",
                timeout=30,
            )
            if not res_play.get("ok"):
                print(f"[portal] play falló: {str(res_play)[:160]}", flush=True)

            # Espera activa a que termine el movimiento actual.
            t0 = time.time()
            visto = False
            while True:
                with _MOV_PROG_LOCK:
                    if not _MOV_PROG.get("activo"):
                        natural = False
                        break
                elapsed = time.time() - t0
                if time.time() >= fin or elapsed > 120:
                    break
                run = _daemon("move/running", "GET", timeout=6)
                data = run.get("data")
                running = (
                    data
                    if isinstance(data, list)
                    else (data.get("running") if isinstance(data, dict) else None)
                )
                if isinstance(running, list):
                    if running:
                        visto = True
                    elif visto or elapsed > 0.6:
                        # Terminó (o el play fue rechazado: se reintenta ya).
                        break
                conocida = _MOV_DUR.get(clave)
                if conocida is not None and elapsed >= conocida - 0.45:
                    time.sleep(0.04)
                else:
                    time.sleep(0.1)
            if not natural:
                break
            if visto and clave not in _MOV_DUR:
                _MOV_DUR[clave] = time.time() - t0
            # Sin pausa: en la siguiente vuelta el play sale de inmediato.
    except Exception as e:  # noqa: BLE001
        print(f"[portal] programa de movimientos terminó con error: {e}", flush=True)
    finally:
        if natural:
            _mov_stop_all(timeout=4.0)
        with _MOV_PROG_LOCK:
            _MOV_PROG["activo"] = False


@app.post("/api/movimientos/programa")
async def api_movimientos_programa_start(request: Request) -> JSONResponse:
    """Inicia un programa: repite un movimiento o encadena varios al azar."""
    body = await request.json()
    try:
        minutos = float(body.get("minutos") or 1)
    except (TypeError, ValueError):
        minutos = 1.0
    minutos = min(max(minutos, 0.25), 60.0)
    dataset = _mov_dataset(str(body.get("categoria") or "bailes"))
    move = str(body.get("move") or "").strip() or None
    modo = "fijo" if move else "variado"

    with _MOV_PROG_LOCK:
        if _MOV_PROG.get("activo"):
            return JSONResponse(
                {"ok": False, "error": "ya hay un programa activo; deténgalo primero"},
                status_code=409,
            )
        fin = time.time() + minutos * 60
        _MOV_PROG.update(
            {
                "activo": True,
                "hasta": fin,
                "total": minutos * 60,
                "modo": modo,
                "move": move or "",
                "dataset": dataset,
            }
        )
    _mov_enable_motors()
    threading.Thread(
        target=_mov_loop, args=(fin, modo, dataset, move), daemon=True
    ).start()
    return JSONResponse({"ok": True, "estado": _mov_programa_estado()})


@app.post("/api/movimientos/programa/stop")
def api_movimientos_programa_stop() -> JSONResponse:
    """Detiene el programa y cancela el movimiento actual (parada total)."""
    with _MOV_PROG_LOCK:
        _MOV_PROG["activo"] = False
    libre = _mov_stop_all()
    return JSONResponse({"ok": True, "candado_libre": libre, "estado": _mov_programa_estado()})


@app.get("/api/movimientos/programa")
def api_movimientos_programa_estado() -> JSONResponse:
    return JSONResponse(_mov_programa_estado())


@app.post("/api/apps/{slug}/stop")
def stop_app(slug: str) -> JSONResponse:
    """Detiene la app en curso; si queda pegada, reinicia el daemon.

    Al quedar sin ninguna app abierta, se reenciende el Asistente.
    """
    _daemon("apps/stop-current-app", "POST", timeout=60)
    stopped, last_state = _wait_app_stopped(timeout=30)
    if not stopped:
        recovered = _restart_daemon_and_wait()
        return JSONResponse({"ok": recovered.get("ok", False), "state": last_state, "recover": recovered})
    _ensure_assistant_running()
    return JSONResponse({"ok": True, "state": last_state})


# ---------- Asistente ----------

ASSISTANT_URL = os.getenv("MI_ASSISTANT_URL", "http://127.0.0.1:7865")
ASSISTANT_APP = "reachy_mini_assistant"


def _assistant(path: str, method: str = "GET", body: dict | None = None, timeout: float = 20.0) -> dict:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.request(method, f"{ASSISTANT_URL}{path}", json=body)
            if resp.status_code >= 400:
                return {"ok": False, "status": resp.status_code, "detail": resp.text[:300]}
            try:
                return {"ok": True, "data": resp.json()}
            except Exception:
                return {"ok": True, "data": {"raw": resp.text[:300]}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _ensure_assistant_running() -> bool:
    """Si no hay ninguna app abierta, arranca el Asistente (con reintentos)."""
    import time

    cur = _current_app()
    if cur is not None and (cur.get("info") or {}).get("name"):
        return True  # ya hay una app; no tocar nada
    for attempt in range(6):
        res = _daemon(f"apps/start-app/{ASSISTANT_APP}", "POST", timeout=90)
        if res.get("ok"):
            print(f"[portal] Asistente iniciado (intento {attempt + 1})", flush=True)
            return True
        detail = str(res.get("detail") or res.get("error") or "")
        if "already running" in detail:
            return True
        print(
            f"[portal] intento {attempt + 1}/6 para iniciar el Asistente falló: {detail[:200]}",
            flush=True,
        )
        time.sleep(5)
    return False


@app.post("/api/assistant/stop-speech")
def assistant_stop_speech() -> JSONResponse:
    return JSONResponse(_assistant("/stop-speech", "POST"))


@app.get("/api/assistant/state")
def assistant_state() -> JSONResponse:
    return JSONResponse(_assistant("/state"))


@app.post("/api/assistant/settings")
async def assistant_settings(request: Request) -> JSONResponse:
    """Guarda la personalidad/ajustes del asistente.

    Escribe SIEMPRE en la config compartida (funciona aunque el asistente no
    esté corriendo, que era el error original) y, si está corriendo, aplica en caliente.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    saved = _write_assistant_config(body)
    live = _assistant("/settings", "POST", body=body)
    return JSONResponse({
        "ok": saved,
        "live": bool(live.get("ok")),
        "error": None if saved else "no se pudo guardar la config",
    })


ASSISTANT_CONFIG_PATH = "/home/pollen/reachy_voice_config.json"
ASSISTANT_SETTINGS_ALLOWED = {
    "personality", "tts_provider", "piper_voice", "azure_voice", "azure_key",
    "azure_region", "elevenlabs_api_key", "elevenlabs_voice_id", "elevenlabs_model",
    "hf_session_url", "groq_api_key", "groq_stt_model", "groq_llm_model",
    "language", "greet_on_start", "greeting", "volume", "voice_provider",
}


def _write_assistant_config(body: dict) -> bool:
    import json as _json

    try:
        data = {}
        if os.path.exists(ASSISTANT_CONFIG_PATH):
            data = _json.loads(open(ASSISTANT_CONFIG_PATH, encoding="utf-8").read())
        for key, value in (body or {}).items():
            if key in ASSISTANT_SETTINGS_ALLOWED:
                data[key] = value
        # Una voz ElevenLabs elegida activa el provider en toda la config
        # compartida (así Clase continua deja de usar la voz HF nativa).
        if str((body or {}).get("elevenlabs_voice_id") or "").strip():
            data["voice_provider"] = "elevenlabs"
        with open(ASSISTANT_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(_json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[portal] no se pudo guardar config del asistente: {e}")
        return False


# Caché corto para no golpear la API de ElevenLabs en cada apertura del panel.
_EL_VOICES_CACHE: dict = {"ts": 0.0, "voices": []}
_EL_SEARCH_CACHE: dict = {"q": None, "ts": 0.0, "voices": []}


def _el_api_key() -> str:
    try:
        data = {}
        if os.path.exists(ASSISTANT_CONFIG_PATH):
            data = json.loads(open(ASSISTANT_CONFIG_PATH, encoding="utf-8").read())
        return str(data.get("elevenlabs_api_key") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _fetch_account_voices(key: str) -> list:
    import time as _t

    if _t.time() - _EL_VOICES_CACHE["ts"] < 60 and _EL_VOICES_CACHE["voices"]:
        return _EL_VOICES_CACHE["voices"]
    resp = httpx.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": key},
        timeout=15,
    )
    resp.raise_for_status()
    voices = []
    for v in resp.json().get("voices", []):
        labels = v.get("labels") or {}
        voices.append({
            "voice_id": v.get("voice_id", ""),
            "name": v.get("name", ""),
            "labels": labels,
        })
    _EL_VOICES_CACHE["ts"] = _t.time()
    _EL_VOICES_CACHE["voices"] = voices
    return voices


def _fetch_shared_voices(key: str, query: str) -> list:
    """Busca en la librería compartida de ElevenLabs (voces que no están en la cuenta)."""
    import time as _t

    if _EL_SEARCH_CACHE["q"] == query and _t.time() - _EL_SEARCH_CACHE["ts"] < 60:
        return _EL_SEARCH_CACHE["voices"]
    resp = httpx.get(
        "https://api.elevenlabs.io/v1/shared-voices",
        params={"search": query, "page_size": 50},
        headers={"xi-api-key": key},
        timeout=15,
    )
    resp.raise_for_status()
    voices = []
    for v in resp.json().get("voices", []):
        voices.append({
            "voice_id": v.get("voice_id", ""),
            "name": v.get("name", ""),
            "labels": {
                "gender": v.get("gender") or "",
                "age": v.get("age") or "",
                "accent": v.get("accent") or "",
                "language": v.get("language") or "",
            },
            "free": bool(v.get("free_users_allowed", False)),
        })
    _EL_SEARCH_CACHE["q"] = query
    _EL_SEARCH_CACHE["ts"] = _t.time()
    _EL_SEARCH_CACHE["voices"] = voices
    return voices


@app.get("/api/elevenlabs/voices")
def api_elevenlabs_voices(search: str = "") -> JSONResponse:
    """Voces ElevenLabs: cuenta propia + (con ?search=) librería compartida."""
    try:
        key = _el_api_key()
        if not key:
            return JSONResponse({"voices": [], "error": "falta elevenlabs_api_key"})
        account = _fetch_account_voices(key)
        query = (search or "").strip()
        if not query:
            return JSONResponse({"voices": account})
        matched = [
            v for v in account
            if query.lower() in (v.get("name") or "").lower()
        ]
        seen = {v["voice_id"] for v in matched}
        shared = []
        try:
            for sv in _fetch_shared_voices(key, query):
                if sv["voice_id"] and sv["voice_id"] not in seen:
                    seen.add(sv["voice_id"])
                    shared.append(sv)
        except Exception:  # noqa: BLE001
            pass  # la librería compartida es opcional; seguimos con lo de la cuenta
        return JSONResponse({"voices": matched + shared})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"voices": [], "error": str(e)}, status_code=502)


LOG_SERVICES = ["reachy-mini-daemon", "mi-portal", "mi-clase", "mi-control", "reachy-mini-bluetooth"]


@app.get("/api/logs")
def api_logs(service: str = "reachy-mini-daemon", lines: int = 200) -> JSONResponse:
    """Devuelve los logs de un servicio systemd del robot (journalctl)."""
    import subprocess

    if service not in LOG_SERVICES:
        service = "reachy-mini-daemon"
    try:
        result = subprocess.run(
            ["journalctl", "-u", service, "-n", str(min(lines, 500)), "--no-pager", "--output=short-iso"],
            timeout=15,
            capture_output=True,
            text=True,
        )
        text = result.stdout or result.stderr
    except Exception as e:  # noqa: BLE001
        text = f"Error leyendo logs: {e}"
    return JSONResponse({"ok": True, "service": service, "lines": text.splitlines()})


@app.post("/api/assistant/greet")
def assistant_greet() -> JSONResponse:
    return JSONResponse(_assistant("/greet", "POST"))


@app.post("/api/assistant/speak")
async def assistant_speak(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return JSONResponse(_assistant("/speak", "POST", body=body))


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


def _auto_start_assistant() -> None:
    """Enciende el Asistente al arrancar y mantiene uno siempre activo.

    - Al iniciar el portal: reintenta hasta que el daemon esté listo.
    - Watchdog permanente (60 s): si no hay ninguna app abierta
      (al prender, o tras cerrar una), se enciende el Asistente solo.
    """
    import time

    time.sleep(20)
    print("[portal] auto-inicio del Asistente (arranque del portal)", flush=True)
    _ensure_assistant_running()
    while True:
        time.sleep(60)
        try:
            cur = _current_app()
            running = cur is not None and bool((cur.get("info") or {}).get("name"))
            if not running:
                print("[portal] watchdog: sin apps abiertas -> Asistente", flush=True)
                _ensure_assistant_running()
        except Exception as e:  # noqa: BLE001
            print(f"[portal] watchdog error: {e}", flush=True)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


# --- Arranque de tareas en segundo plano -------------------------------------------------
# IMPORTANTE: el servicio systemd ejecuta launcher.py (no main()), así que estos
# hilos deben iniciarse al IMPORTAR el módulo. Un flag evita duplicados.
_BACKGROUND_STARTED = False


def _start_background_tasks() -> None:
    global _BACKGROUND_STARTED
    if _BACKGROUND_STARTED:
        return
    _BACKGROUND_STARTED = True
    import threading

    threading.Thread(target=_ensure_hotspot_if_offline, daemon=True, name="hotspot-ensure").start()
    threading.Thread(target=_auto_start_assistant, daemon=True, name="assistant-autostart").start()


_start_background_tasks()


if __name__ == "__main__":
    main()
