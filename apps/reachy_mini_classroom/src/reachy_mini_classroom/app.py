"""Reachy daemon entry point and web control, isolated from the original Hub."""
import logging
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reachy_mini import ReachyMiniApp

from .backend import HubBackend
from .controller import Controller
from .storage import Store
from .motion import HubMotion


class StartBody(BaseModel):
    title: str = Field(default='Clase', max_length=160)
    session: str | None = None


class SessionBody(BaseModel):
    session: str | None = None


def install_routes(app, controller):
    static = Path(__file__).parent / 'static'
    # As in the Hub, replace the framework's default app root.
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', '') not in ('/', '/static')]
    app.mount('/static', StaticFiles(directory=static), name='class-static')

    @app.get('/')
    def index():
        return FileResponse(static / 'index.html', headers={'Cache-Control': 'no-store'})

    @app.get('/api/status')
    def status():
        return controller.status()

    @app.get('/api/sessions/{sid}')
    def detail(sid: str):
        try:
            return controller.store.detail(sid)
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    @app.get('/api/sessions/{sid}/transcript.txt')
    def download(sid: str):
        try:
            # Same stable URL as before Responder ran, with no validators: without this the
            # browser reuses the copy it downloaded when the class still had no summary.
            return PlainTextResponse(controller.store.transcript(sid), headers={
                'Content-Disposition': 'attachment; filename="transcripcion.txt"',
                'Cache-Control': 'no-store, must-revalidate', 'Pragma': 'no-cache'})
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    def action(fn, *args):
        try:
            fn(*args)
            return {'ok': True}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post('/api/start')
    def start(body: StartBody):
        return action(controller.start, body.title, body.session)

    @app.post('/api/pause')
    def pause():
        return action(controller.pause)

    @app.post('/api/respond')
    def respond(body: SessionBody):
        return action(controller.respond, body.session)

    @app.post('/api/pause-voice')
    def pause_voice():
        return action(controller.pause_voice)


class ClassroomApp(ReachyMiniApp):
    custom_app_url = 'http://0.0.0.0:8093/'
    dont_start_webserver = False

    def run(self, reachy_mini, stop_event):
        logging.basicConfig(level=logging.INFO)
        # Reuse the installed Hub's selected connection and voice without copying secrets.
        from dotenv import load_dotenv
        import reachy_mini_hub
        hub_path = Path(reachy_mini_hub.__file__).parent
        load_dotenv(hub_path / '.env', override=False)
        from reachy_mini_hub.startup_settings import read_startup_settings
        settings = read_startup_settings(hub_path)
        if settings.voice:
            os.environ.setdefault('CLASSROOM_VOICE', settings.voice)
        root = Path(os.getenv('CLASSROOM_DATA_DIR', str(Path.home() / 'recordings' / 'classroom')))
        from reachy_mini_hub.audio.startup_config import apply_audio_startup_config
        apply_audio_startup_config(reachy_mini)
        controller = Controller(Store(root), HubBackend(reachy_mini), reachy_mini.media, stop_event,
                                motion=HubMotion(reachy_mini))
        install_routes(self.settings_app, controller)
        try:
            stop_event.wait()
        finally:
            controller.shutdown()


if __name__ == '__main__':
    ClassroomApp().wrapped_run()
