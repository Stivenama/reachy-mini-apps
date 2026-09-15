"""Reachy Mini Audio Mixer: cambia el dispositivo de salida de audio.

App instalable como tile en Reachy Mini Control. Expone un panel web que
conmuta el ~/.asoundrc entre el parlante interno (tarjeta 0) y el dispositivo
USB-C externo (tarjeta 3), ajusta su volumen y reproduce un sonido de prueba.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from reachy_mini import ReachyMini, ReachyMiniApp

from reachy_mini_audio_mixer.audio_switch import (
    DEVICES,
    get_current_output,
    get_volume,
    play_test_sound,
    set_audio_output,
    set_volume,
)


class ReachyMiniAudioMixerApp(ReachyMiniApp):
    """Tile del controlador para elegir la salida de audio."""

    custom_app_url = "http://0.0.0.0:7862/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Montar la API y esperar a que se detenga la app."""
        app = self.settings_app
        if app is not None:
            self._register_routes(app)

        try:
            reachy_mini.wake_up()
        except Exception:
            pass

        stop_event.wait()

    def _register_routes(self, app: FastAPI) -> None:
        @app.get("/api/outputs")
        def _outputs() -> JSONResponse:
            return JSONResponse({"devices": DEVICES})

        @app.get("/api/output")
        def _output() -> JSONResponse:
            current = get_current_output()
            return JSONResponse({"output": current, "volume": get_volume(current)})

        @app.post("/api/output")
        async def _set_output(request: Request) -> JSONResponse:
            payload = await request.json()
            device_id = str(payload.get("device", "")).strip()
            if device_id not in {device["id"] for device in DEVICES}:
                return JSONResponse({"error": "Dispositivo inválido"}, status_code=400)
            set_audio_output(device_id)
            return JSONResponse({"output": device_id, "volume": get_volume(device_id)})

        @app.get("/api/volume")
        def _volume() -> JSONResponse:
            current = get_current_output()
            return JSONResponse({"volume": get_volume(current), "output": current})

        @app.post("/api/volume")
        async def _set_volume(request: Request) -> JSONResponse:
            payload = await request.json()
            try:
                volume = int(payload.get("volume", 50))
            except (TypeError, ValueError):
                return JSONResponse({"error": "Volumen inválido"}, status_code=400)
            current = get_current_output()
            set_volume(volume, current)
            return JSONResponse({"volume": get_volume(current), "output": current})

        @app.post("/api/test")
        def _test() -> JSONResponse:
            play_test_sound()
            return JSONResponse({"ok": True})

        @app.get("/api/health")
        def _health() -> JSONResponse:
            return JSONResponse(
                {"ok": True, "output": get_current_output(), "volume": get_volume(get_current_output())}
            )


if __name__ == "__main__":
    app = ReachyMiniAudioMixerApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()