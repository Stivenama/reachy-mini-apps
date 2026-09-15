"""Reachy Hub timeline: emociones programadas sobre una lÃ­nea de tiempo reproducible.

El scheduler ejecuta cada emociÃ³n en su segundo marcado usando la API de
recorded moves del daemon (la misma que usa el controlador), y soporta
play/pausa/reanudar/detener + guardado/carga de lÃ­neas en JSON.
"""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Request

logger = logging.getLogger(__name__)

EMOTIONS_DATASET = "pollen-robotics/reachy-mini-emotions-library"
MIN_DURATION_S = 21 * 60
MAX_DURATION_S = 120 * 60
DEFAULT_DURATION_S = 21 * 60
_POLL_INTERVAL_S = 0.2


class TimelineController:
    """Owns timeline state and fires each emotion at its scheduled second."""

    def __init__(self, daemon_base_url: str, timelines_dir: Path) -> None:
        self._base = daemon_base_url.rstrip("/")
        self._dir = timelines_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self.duration_s = DEFAULT_DURATION_S
        self.events: list[dict[str, Any]] = []
        self._playing = False
        self._position_s = 0.0
        self._started_at = 0.0
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fired: set[str] = set()
        self._fire_emotion_cb: Any = None
        self._stop_moves_cb: Any = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the controller to the app's asyncio loop (HTTP threads hop onto it)."""
        self._loop = loop

    def set_motion_hooks(self, fire_emotion: Any, stop_moves: Any) -> None:
        """Inject the app-side emotion playback (queue_move) and stop callbacks."""
        self._fire_emotion_cb = fire_emotion
        self._stop_moves_cb = stop_moves

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the controller to the app's asyncio loop (HTTP threads hop onto it)."""
        self._loop = loop

    @property
    def playing(self) -> bool:
        return self._playing

    def position_s(self) -> float:
        if not self._playing:
            return self._position_s
        return time.monotonic() - self._started_at

    def state_payload(self) -> dict[str, Any]:
        return {
            "duration": self.duration_s,
            "events": sorted(self.events, key=lambda event: event["time"]),
            "playing": self._playing,
            "position": round(self.position_s(), 1),
        }

    async def emotions(self) -> list[str]:
        """Return the emotion move names from the daemon's emotions dataset."""
        path = f"/api/move/recorded-move-datasets/list/{quote(EMOTIONS_DATASET, safe='')}"
        try:
            async with httpx.AsyncClient(base_url=self._base, timeout=15) as client:
                response = await client.get(path)
                response.raise_for_status()
                names = response.json()
                return [name for name in names if isinstance(name, str)]
        except Exception as error:
            logger.warning("Failed to list emotions from daemon: %s", error)
            return []

    async def _play_emotion(self, emotion: str) -> None:
        if self._fire_emotion_cb is None:
            logger.warning("Timeline emotion %s: no playback hook wired", emotion)
            return
        try:
            self._fire_emotion_cb(emotion)
            logger.info("Timeline fired emotion %s", emotion)
        except Exception as error:
            logger.warning("Failed to queue emotion %s: %s", emotion, error)

    async def _stop_running_moves(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=self._base, timeout=10) as client:
                running = (await client.get("/api/move/running")).json()
                for move_uuid in running:
                    try:
                        await client.post("/api/move/stop", json={"uuid": str(move_uuid)})
                    except Exception as error:
                        logger.debug("Failed to stop move %s: %s", move_uuid, error)
        except Exception as error:
            logger.warning("Failed to query running moves: %s", error)

    async def _run(self) -> None:
        self._started_at = time.monotonic() - self._position_s
        self._fired = {event["id"] for event in self.events if event["time"] < self._position_s}
        try:
            while self._playing:
                position = time.monotonic() - self._started_at
                for event in sorted(self.events, key=lambda item: item["time"]):
                    if event["id"] not in self._fired and position >= event["time"]:
                        self._fired.add(event["id"])
                        await self._play_emotion(event["emotion"])
                if position >= self.duration_s:
                    self._playing = False
                    self._position_s = self.duration_s
                    break
                await asyncio.sleep(_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _spawn(self) -> None:
        self._task = asyncio.create_task(self._run())

    def play(self) -> None:
        if self._playing or self._loop is None:
            return
        self._playing = True
        # Fijar el reloj de forma síncrona: position_s() se lee desde otros
        # hilos antes de que la tarea del loop arranque.
        self._started_at = time.monotonic() - self._position_s
        try:
            asyncio.run_coroutine_threadsafe(self._spawn(), self._loop)
        except Exception as error:
            logger.warning("Failed to schedule timeline task: %s", error)
            self._playing = False

    def _cancel_task(self) -> None:
        task = self._task
        self._task = None
        if task is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(task.cancel)

    def _stop_moves_on_loop(self) -> None:
        if self._stop_moves_cb is not None:
            try:
                self._stop_moves_cb()
            except Exception as error:
                logger.warning("Failed to stop timeline moves: %s", error)
            return
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._stop_running_moves(), self._loop)
        except RuntimeError:
            logger.debug("Event loop closed; skipping move stop")

    def pause(self) -> None:
        if not self._playing:
            return
        self._position_s = time.monotonic() - self._started_at
        self._playing = False
        self._cancel_task()
        self._stop_moves_on_loop()

    def stop(self) -> None:
        self._playing = False
        self._position_s = 0.0
        self._fired.clear()
        self._cancel_task()
        self._stop_moves_on_loop()

    def configure(self, duration_s: int) -> None:
        self.stop()
        self.duration_s = int(duration_s)
        self.events = []

    def add_event(self, time_s: int, emotion: str) -> dict[str, Any]:
        event = {"id": uuid.uuid4().hex[:8], "time": int(time_s), "emotion": emotion}
        self.events.append(event)
        return event

    def remove_event(self, event_id: str) -> bool:
        before = len(self.events)
        self.events = [event for event in self.events if event["id"] != event_id]
        return len(self.events) < before

    def save(self, name: str) -> bool:
        if not name.strip():
            return False
        path = self._dir / f"{name.strip()}.json"
        path.write_text(
            json.dumps({"name": name.strip(), "duration_s": self.duration_s, "events": self.events}, indent=2),
            encoding="utf-8",
        )
        return True

    def list_saved(self) -> list[str]:
        return sorted(path.stem for path in self._dir.glob("*.json"))

    def load(self, name: str) -> bool:
        path = self._dir / f"{name}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Failed to load timeline %s: %s", name, error)
            return False
        self.stop()
        self.duration_s = int(data.get("duration_s", DEFAULT_DURATION_S))
        self.events = [
            {"id": str(event.get("id") or uuid.uuid4().hex[:8]), "time": int(event["time"]), "emotion": str(event["emotion"])}
            for event in data.get("events", [])
        ]
        return True

    def delete_saved(self, name: str) -> bool:
        path = self._dir / f"{name}.json"
        if not path.exists():
            return False
        path.unlink()
        return True


def register_timeline_routes(app: Any, controller: TimelineController) -> None:
    """Mount the /api/timeline HTTP endpoints on the settings app."""

    @app.get("/api/timeline/emotions")
    async def _timeline_emotions() -> dict[str, Any]:
        return {"emotions": await controller.emotions()}

    @app.get("/api/timeline/state")
    def _timeline_state() -> dict[str, Any]:
        return controller.state_payload()

    @app.post("/api/timeline/configure")
    async def _timeline_configure(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            duration_s = int(payload.get("duration", DEFAULT_DURATION_S))
        except (TypeError, ValueError):
            return {"error": "DuraciÃ³n invÃ¡lida"}, 400
        if duration_s < MIN_DURATION_S or duration_s > MAX_DURATION_S:
            return {"error": f"DuraciÃ³n entre {MIN_DURATION_S // 60} y {MAX_DURATION_S // 60} minutos"}, 400
        controller.configure(duration_s)
        return controller.state_payload()

    @app.post("/api/timeline/events")
    async def _timeline_add_event(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            time_s = int(payload.get("time", -1))
        except (TypeError, ValueError):
            return {"error": "Tiempo invÃ¡lido"}, 400
        emotion = str(payload.get("emotion", "")).strip()
        if time_s < 0 or time_s > controller.duration_s:
            return {"error": "Tiempo fuera de la lÃ­nea"}, 400
        if not emotion:
            return {"error": "EmociÃ³n requerida"}, 400
        controller.add_event(time_s, emotion)
        return controller.state_payload()

    @app.delete("/api/timeline/events")
    async def _timeline_remove_event(request: Request) -> dict[str, Any]:
        payload = await request.json()
        controller.remove_event(str(payload.get("id", "")))
        return controller.state_payload()

    @app.post("/api/timeline/play")
    def _timeline_play() -> dict[str, Any]:
        controller.play()
        return controller.state_payload()

    @app.post("/api/timeline/pause")
    def _timeline_pause() -> dict[str, Any]:
        controller.pause()
        return controller.state_payload()

    @app.post("/api/timeline/stop")
    def _timeline_stop() -> dict[str, Any]:
        controller.stop()
        return controller.state_payload()

    @app.get("/api/timeline/saved")
    def _timeline_saved() -> dict[str, Any]:
        return {"saved": controller.list_saved()}

    @app.post("/api/timeline/save")
    async def _timeline_save(request: Request) -> dict[str, Any]:
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
        if not name or not controller.save(name):
            return {"error": "Nombre invÃ¡lido"}, 400
        return {"ok": True, "saved": controller.list_saved()}

    @app.post("/api/timeline/load")
    async def _timeline_load(request: Request) -> dict[str, Any]:
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
        if not controller.load(name):
            return {"error": "LÃ­nea no encontrada"}, 404
        return controller.state_payload()

    @app.delete("/api/timeline/saved")
    async def _timeline_delete(request: Request) -> dict[str, Any]:
        payload = await request.json()
        controller.delete_saved(str(payload.get("name", "")))
        return {"ok": True, "saved": controller.list_saved()}