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
from .camera_routes import install_camera_routes
from .video_routes import install_video_routes


class StartBody(BaseModel):
    title: str = Field(default='Clase', max_length=160)
    session: str | None = None


class SessionBody(BaseModel):
    session: str | None = None


class VoiceBody(BaseModel):
    voice_id: str = Field(default='', max_length=64)


class RoboticBody(BaseModel):
    robotic: bool = False


SHARED_VOICE_CONFIG = '/home/pollen/reachy_voice_config.json'
_EL_CACHE = {'ts': 0.0, 'voices': []}
_EL_SEARCH = {'q': None, 'ts': 0.0, 'voices': []}


def _read_voice_cfg() -> dict:
    import json
    try:
        return json.loads(open(SHARED_VOICE_CONFIG, encoding='utf-8').read())
    except Exception:
        return {}


def _fetch_account_voices(key: str) -> list:
    import time as _t
    import httpx
    if _t.time() - _EL_CACHE['ts'] < 60 and _EL_CACHE['voices']:
        return _EL_CACHE['voices']
    r = httpx.get('https://api.elevenlabs.io/v1/voices',
                  headers={'xi-api-key': key}, timeout=15)
    r.raise_for_status()
    voices = [{'voice_id': v.get('voice_id', ''), 'name': v.get('name', ''),
               'labels': v.get('labels') or {}}
              for v in r.json().get('voices', [])]
    _EL_CACHE['ts'] = _t.time()
    _EL_CACHE['voices'] = voices
    return voices


def _fetch_shared_voices(key: str, query: str) -> list:
    import time as _t
    import httpx
    if _EL_SEARCH['q'] == query and _t.time() - _EL_SEARCH['ts'] < 60:
        return _EL_SEARCH['voices']
    r = httpx.get('https://api.elevenlabs.io/v1/shared-voices',
                  params={'search': query, 'page_size': 50},
                  headers={'xi-api-key': key}, timeout=15)
    r.raise_for_status()
    voices = [{'voice_id': v.get('voice_id', ''), 'name': v.get('name', ''),
               'labels': {'gender': v.get('gender') or '',
                          'age': v.get('age') or '',
                          'accent': v.get('accent') or '',
                          'language': v.get('language') or ''},
               'free': bool(v.get('free_users_allowed', False))}
              for v in r.json().get('voices', [])]
    _EL_SEARCH['q'] = query
    _EL_SEARCH['ts'] = _t.time()
    _EL_SEARCH['voices'] = voices
    return voices


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

    @app.get('/api/voices/elevenlabs')
    def elevenlabs_voices(search: str = ''):
        key = (_read_voice_cfg().get('elevenlabs_api_key') or '').strip()
        if not key:
            return {'voices': [], 'error': 'Falta elevenlabs_api_key'}
        try:
            account = _fetch_account_voices(key)
            query = (search or '').strip()
            if not query:
                return {'voices': account}
            matched = [v for v in account
                       if query.lower() in (v.get('name') or '').lower()]
            seen = {v['voice_id'] for v in matched}
            shared = []
            try:
                for sv in _fetch_shared_voices(key, query):
                    if sv['voice_id'] and sv['voice_id'] not in seen:
                        seen.add(sv['voice_id'])
                        shared.append(sv)
            except Exception:
                pass
            return {'voices': matched + shared}
        except Exception as e:
            return {'voices': [], 'error': str(e)}

    @app.get('/api/voices/native')
    def native_voices():
        """Voces nativas del conversation app (HF): lista, actual y provider activo."""
        try:
            from reachy_mini_hub.config import get_available_voices, get_default_voice
            voices = get_available_voices()
            default = get_default_voice()
        except Exception:
            voices, default = [], ''
        current = os.environ.get('CLASSROOM_VOICE', '') or default or ''
        provider = (_read_voice_cfg().get('voice_provider') or 'elevenlabs')
        return {'voices': voices, 'current': current, 'provider': provider}

    @app.post('/api/voices/native')
    def native_voice_set(body: VoiceBody):
        import json
        voice = (body.voice_id or '').strip()
        if not voice:
            raise HTTPException(400, 'voice requerida')
        # Aplica ya a las próximas conexiones realtime de esta app.
        os.environ['CLASSROOM_VOICE'] = voice
        # Persiste en los startup settings del Hub (sobrevive reinicios de ambas apps).
        try:
            import reachy_mini_hub
            from reachy_mini_hub.startup_settings import read_startup_settings, write_startup_settings
            hub_path = Path(reachy_mini_hub.__file__).parent
            existing = read_startup_settings(hub_path)
            write_startup_settings(hub_path, profile=existing.profile, voice=voice)
        except Exception:
            pass
        # La clase pasa a usar la voz HF (deja de sintetizar con ElevenLabs).
        try:
            data = _read_voice_cfg()
            data['voice_provider'] = 'hf'
            open(SHARED_VOICE_CONFIG, 'w', encoding='utf-8').write(
                json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            pass
        return {'ok': True, 'voice': voice, 'provider': 'hf'}

    @app.get('/api/voices/elevenlabs/current')
    def elevenlabs_voice_current():
        cfg = _read_voice_cfg()
        return {
            'voice_id': (cfg.get('elevenlabs_voice_id') or '').strip(),
            'provider': (cfg.get('voice_provider') or 'elevenlabs'),
        }

    @app.post('/api/voices/elevenlabs')
    def elevenlabs_voice_set(body: VoiceBody):
        import json
        vid = (body.voice_id or '').strip()
        if not vid:
            raise HTTPException(400, 'voice_id requerido')
        try:
            data = _read_voice_cfg()
            data['elevenlabs_voice_id'] = vid
            data['voice_provider'] = 'elevenlabs'
            open(SHARED_VOICE_CONFIG, 'w', encoding='utf-8').write(
                json.dumps(data, ensure_ascii=False, indent=2))
            return {'ok': True, 'voice_id': vid, 'provider': 'elevenlabs'}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.get('/api/voices/robotic')
    def robotic_get():
        return {'robotic': bool(_read_voice_cfg().get('elevenlabs_robotic'))}

    @app.post('/api/voices/robotic')
    def robotic_set(body: RoboticBody):
        import json
        try:
            data = _read_voice_cfg()
            data['elevenlabs_robotic'] = bool(body.robotic)
            open(SHARED_VOICE_CONFIG, 'w', encoding='utf-8').write(
                json.dumps(data, ensure_ascii=False, indent=2))
            return {'ok': True, 'robotic': bool(body.robotic)}
        except Exception as e:
            raise HTTPException(500, str(e))


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
        install_camera_routes(self.settings_app, reachy_mini.media, controller.motion.view)
        install_video_routes(self.settings_app)
        try:
            stop_event.wait()
        finally:
            controller.shutdown()


if __name__ == '__main__':
    ClassroomApp().wrapped_run()
