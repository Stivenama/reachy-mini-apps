"""Proxy video signalling only; the daemon sends its encoded stream directly."""
import asyncio
import contextlib
import json
from urllib.parse import urlsplit
from fastapi import WebSocket


def sanitize_sdp(message):
    sdp = message.get('sdp') if isinstance(message, dict) else None
    if not isinstance(sdp, dict) or not isinstance(sdp.get('sdp'), str):
        return message
    lines = []
    for line in sdp['sdp'].split('\r\n'):
        if line.startswith('a=candidate:'):
            fields = line.split()
            if 'typ relay' in line.lower() or (len(fields)>4 and fields[4].startswith('127.')):
                continue
        lines.append(line)
    return {**message, 'sdp':{**sdp, 'sdp':'\r\n'.join(lines)}}


def install_video_routes(app):
    @app.websocket('/ws/video')
    async def video(ws: WebSocket):
        origin = urlsplit(ws.headers.get('origin', ''))
        if origin.scheme not in ('http','https') or origin.netloc != ws.headers.get('host'):
            await ws.close(code=1008)
            return
        from websockets.asyncio.client import connect
        await ws.accept()
        tasks = []
        try:
            async with connect('ws://127.0.0.1:8443', max_size=65536, open_timeout=5,
                               ping_interval=None, ping_timeout=None, close_timeout=3) as upstream:
                async def to_robot():
                    while True:
                        raw = await ws.receive_text()
                        if len(raw) > 65536:
                            raise ValueError('Signalling message too large')
                        data = json.loads(raw)
                        if not isinstance(data, dict):
                            raise ValueError('Invalid signalling')
                        if data.get('type') == 'setPeerStatus' and data.get('roles') != ['listener']:
                            raise ValueError('Only camera viewers are supported')
                        if data.get('type') in ('setPeerStatus','list','startSession','peer','endSession'):
                            await upstream.send(raw)

                async def to_browser():
                    async for raw in upstream:
                        await ws.send_json(sanitize_sdp(json.loads(raw)))

                tasks = [asyncio.create_task(to_robot()), asyncio.create_task(to_browser())]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done: task.result()
        except Exception:
            # The client reconnects or falls back to camera snapshots.
            pass
        finally:
            for task in tasks: task.cancel()
            if tasks: await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception): await ws.close()
