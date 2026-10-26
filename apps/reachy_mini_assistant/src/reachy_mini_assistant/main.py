"""Entrypoint del Asistente de Reachy Mini (app manejada por el daemon)."""

from __future__ import annotations

import threading

from reachy_mini import ReachyMini, ReachyMiniApp

from reachy_mini_assistant.assistant import run_assistant


class ReachyMiniAssistantApp(ReachyMiniApp):
    """Asistente hablante conversacional (voz + comandos)."""

    custom_app_url: str | None = "http://0.0.0.0:7865/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        run_assistant(
            reachy_mini,
            stop_event,
            settings_app=self.settings_app,
        )


def main() -> None:
    app = ReachyMiniAssistantApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    main()
