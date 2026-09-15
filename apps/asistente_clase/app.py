"""Asistente de Clase — app local (FastAPI) independiente del robot."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import classroom_client
import jobs
import notebooklm_engine as nb
import settings as S
import accounts
from db import Database
from google_auth import classroom_service, get_credentials

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"

app = FastAPI(title="Asistente de Clase")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
db = Database()


def _config() -> dict:
    return S.load()


def _txt_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


class ActiveBody(BaseModel):
    profile_id: str


class AddAccountBody(BaseModel):
    email: str


class NotebookBody(BaseModel):
    profile_id: str
    title: str


class TopicBody(BaseModel):
    profile_id: str
    course_id: str
    name: str


class ProcessBody(BaseModel):
    profile_id: str
    txt_name: str
    notebook_id: str = ""
    new_notebook_title: str = ""
    course_id: str = ""
    topic_id: str = ""
    new_topic: str = ""
    report: bool = True
    infographic: bool = True
    video: bool = True
    transcript: bool = False
    publish: bool = False


def _profile_google(profile_id: str, interactive: bool = False):
    config = _config()
    profile = S.get_profile(config, profile_id)
    creds = get_credentials(
        S.token_path(config, profile), S.client_secret_path(config), interactive=interactive
    )
    return config, profile, creds


@app.on_event("startup")
def _startup() -> None:
    jobs.start(db)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC / "index.html"), headers={"Cache-Control": "no-store"})


@app.get("/api/config")
def api_config() -> JSONResponse:
    config = _config()
    return JSONResponse(
        {
            "profiles": [
                {"id": pid, "label": entry.get("label", pid)}
                for pid, entry in config["profiles"].items()
            ],
            "active_profile": config.get("active_profile"),
            "transcripts_dir": config["transcripts_dir"],
            "drive_root": config["drive_root"],
            "default_topic": config["default_topic"],
        }
    )


@app.post("/api/config/active")
def api_set_active(body: ActiveBody) -> JSONResponse:
    config = _config()
    if body.profile_id not in config["profiles"]:
        raise HTTPException(400, "perfil desconocido")
    config["active_profile"] = body.profile_id
    S.save(config)
    return JSONResponse({"ok": True})


@app.post("/api/profiles")
def api_add_profile(body: AddAccountBody) -> JSONResponse:
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "correo no válido")
    ok, detail = accounts.start(email)
    if not ok:
        return JSONResponse({"ok": False, "error": detail})
    return JSONResponse({"ok": True, "profile_id": detail})


@app.get("/api/profiles/status")
def api_profile_status() -> JSONResponse:
    return JSONResponse(accounts.status())


@app.post("/api/profiles/relogin")
def api_relogin(body: ActiveBody) -> JSONResponse:
    ok, detail = accounts.relogin(body.profile_id)
    if not ok:
        return JSONResponse({"ok": False, "error": detail})
    return JSONResponse({"ok": True})


@app.get("/api/transcripts")
def api_transcripts() -> JSONResponse:
    config = _config()
    folder = Path(config["transcripts_dir"])
    if not folder.exists():
        return JSONResponse({"files": [], "dir": str(folder)})
    files = sorted(p.name for p in folder.glob("*.txt"))
    return JSONResponse({"files": files, "dir": str(folder)})


@app.get("/api/notebooks")
def api_notebooks(profile_id: str) -> JSONResponse:
    config = _config()
    profile = S.get_profile(config, profile_id)
    try:
        items = nb.list_notebooks(profile["notebooklm_profile"])
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(error)})
    return JSONResponse({"ok": True, "notebooks": items})


@app.post("/api/notebooks")
def api_create_notebook(body: NotebookBody) -> JSONResponse:
    config = _config()
    profile = S.get_profile(config, body.profile_id)
    try:
        notebook_id = nb.create_notebook(profile["notebooklm_profile"], body.title)
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(error)})
    return JSONResponse({"ok": True, "notebook_id": notebook_id})


@app.get("/api/courses")
def api_courses(profile_id: str) -> JSONResponse:
    try:
        _, _, creds = _profile_google(profile_id)
        service = classroom_service(creds)
        return JSONResponse({"ok": True, "courses": classroom_client.list_courses(service)})
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(error)})


@app.get("/api/topics")
def api_topics(profile_id: str, course_id: str) -> JSONResponse:
    try:
        _, _, creds = _profile_google(profile_id)
        service = classroom_service(creds)
        return JSONResponse({"ok": True, "topics": classroom_client.list_topics(service, course_id)})
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(error)})


@app.post("/api/topics")
def api_create_topic(body: TopicBody) -> JSONResponse:
    try:
        _, _, creds = _profile_google(body.profile_id)
        service = classroom_service(creds)
        topic_id = classroom_client.find_or_create_topic(service, body.course_id, body.name)
        return JSONResponse({"ok": True, "topic_id": topic_id})
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(error)})


@app.post("/api/process")
def api_process(body: ProcessBody) -> JSONResponse:
    config = _config()
    txt_path = Path(config["transcripts_dir"]) / body.txt_name
    if not txt_path.exists():
        raise HTTPException(404, "No encontré el archivo de transcripción")
    class_id = jobs.class_id_for(body.profile_id, body.course_id, str(txt_path))
    current = db.get_class(class_id)
    if current and current["status"] in ("queued", "running", "uploading", "generating"):
        return JSONResponse({"ok": False, "error": "Esta clase ya se está procesando"})

    topic_id = body.topic_id
    if topic_id == "__new__" and body.new_topic and body.course_id:
        _, _, creds = _profile_google(body.profile_id)
        service = classroom_service(creds)
        topic_id = classroom_client.find_or_create_topic(service, body.course_id, body.new_topic)

    auto_notebook = body.notebook_id in ("", "__new__", "__auto__")
    notebook_id = "" if auto_notebook else body.notebook_id
    if auto_notebook:
        notebook_source_id = ""
    elif current and current["notebook_id"] == notebook_id:
        notebook_source_id = current["notebook_source_id"]
    else:
        notebook_source_id = ""
    options = {
        "report": body.report,
        "infographic": body.infographic,
        "video": body.video,
        "transcript": body.transcript,
        "publish": body.publish,
        "research": True,
    }
    db.upsert_class(
        class_id,
        title=Path(body.txt_name).stem,
        txt_name=body.txt_name,
        txt_path=str(txt_path),
        txt_hash=_txt_hash(txt_path),
        profile_id=body.profile_id,
        notebook_id=notebook_id,
        notebook_source_id=notebook_source_id,
        classroom_course_id=body.course_id,
        topic_id=topic_id,
        options=json.dumps(options, ensure_ascii=False),
        status="queued",
        report_status="pending",
        infographic_status="pending",
        video_status="pending",
        classroom_status="pending",
        research_status="pending",
        research_text_url="",
        research_text_title="",
        research_video_url="",
        research_video_title="",
        pending_status="pending",
        pending_text="",
        error="",
    )
    jobs.enqueue(class_id)
    return JSONResponse({"ok": True, "class_id": class_id})


@app.get("/api/classes")
def api_classes() -> JSONResponse:
    return JSONResponse({"classes": db.list_classes()})


@app.post("/api/classes/{class_id}/retry")
def api_retry(class_id: str) -> JSONResponse:
    if not db.get_class(class_id):
        raise HTTPException(404, "clase desconocida")
    db.update_class(
        class_id,
        status="queued",
        report_status="pending",
        infographic_status="pending",
        video_status="pending",
        classroom_status="pending",
        research_status="pending",
        research_text_url="",
        research_text_title="",
        research_video_url="",
        research_video_title="",
        pending_status="pending",
        pending_text="",
        error="",
    )
    jobs.enqueue(class_id)
    return JSONResponse({"ok": True})


@app.post("/api/classes/{class_id}/cancel")
def api_cancel(class_id: str) -> JSONResponse:
    if not db.get_class(class_id):
        raise HTTPException(404, "clase desconocida")
    jobs.cancel(db, class_id)
    return JSONResponse({"ok": True})


def main() -> None:
    import uvicorn

    port = int(os.getenv("ASISTENTE_PORT", str(_config().get("port", 8099))))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
