"""Independent camera/control routes; never stop or reopen the audio device."""
import threading
import time
from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field


class Owner(BaseModel):
    owner: str = Field(min_length=16, max_length=100)


class Position(Owner):
    sequence: int = Field(ge=0)
    yaw: float = Field(ge=-25, le=25, allow_inf_nan=False)
    pitch: float = Field(ge=-20, le=20, allow_inf_nan=False)
    body: float = Field(ge=-45, le=45, allow_inf_nan=False)
    natural: bool = True


class CameraFrames:
    def __init__(self, media):
        self.media = media
        self.lock = threading.Lock()
        self.frame = None
        self.last = 0.0

    def get(self):
        with self.lock:
            now = time.monotonic()
            if now - self.last >= .05 or self.frame is None:
                # SDK shares its existing camera stream; no competing device capture.
                self.frame = self.media.get_frame_jpeg()
                self.last = now
            if not self.frame:
                raise ValueError('La cámara todavía no entrega imagen. Se reintentará automáticamente.')
            return self.frame


def install_camera_routes(app, media, control):
    camera = CameraFrames(media)

    def same_origin(request):
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'Abre los controles desde la página de Clase continua.')

    def perform(request, fn, *args):
        same_origin(request)
        try:
            return fn(*args) or {'ok': True}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get('/api/camera/frame')
    def frame():
        try:
            return Response(camera.get(), media_type='image/jpeg',
                            headers={'Cache-Control': 'no-store'})
        except Exception:
            raise HTTPException(503, 'Cámara no disponible. Reconectando…')

    @app.get('/api/view/status')
    def status():
        return control.status()

    @app.post('/api/view/claim')
    def claim(body: Owner, request: Request):
        return perform(request, control.claim, body.owner)

    @app.post('/api/view/position')
    def position(body: Position, request: Request):
        return perform(request, control.command, body.owner, body.sequence,
                       body.yaw, body.pitch, body.body, body.natural)

    @app.post('/api/view/release')
    def release(body: Owner, request: Request):
        return perform(request, control.release, body.owner)
