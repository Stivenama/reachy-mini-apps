"""Orquestador asíncrono: sube fuente, genera artefactos, sube a Drive y publica."""

from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
import traceback
from pathlib import Path

import classroom_client
import drive_client
import notebooklm_engine as nb
import academic_research
import settings as S
from google_auth import classroom_service, drive_service, get_credentials

BASE = Path(__file__).resolve().parent
DELIVERIES = BASE / "descargas"

KINDS = {
    "report": ("report_status", "report_path", "report_drive_id", "informe.md"),
    "infographic": ("infographic_status", "infographic_path", "infographic_drive_id", "infografia.png"),
    "video": ("video_status", "video_path", "video_drive_id", "video.mp4"),
}

_queue: "queue.Queue[str]" = queue.Queue()
_cancelled: set[str] = set()
_cancel_lock = threading.Lock()


def class_id_for(profile_id: str, course_id: str, txt_path: str) -> str:
    key = f"{profile_id}|{course_id}|{txt_path}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()[:16]


def enqueue(class_id: str) -> None:
    with _cancel_lock:
        _cancelled.discard(class_id)
    _queue.put(class_id)


def cancel(db, class_id: str) -> None:
    with _cancel_lock:
        _cancelled.add(class_id)
    db.update_class(class_id, status="cancelled", error="")
    nb.kill_running()


def _is_cancelled(class_id: str) -> bool:
    with _cancel_lock:
        return class_id in _cancelled


def start(db) -> None:
    for class_id in db.reset_incomplete():
        enqueue(class_id)
    for row in db.list_classes():
        if row["status"] in ("pending", "queued"):
            enqueue(row["class_id"])
    threading.Thread(target=_loop, args=(db,), daemon=True).start()


def _loop(db) -> None:
    while True:
        try:
            class_id = _queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            _process(db, class_id)
        except Exception as error:  # noqa: BLE001
            db.update_class(class_id, status="failed", error=str(error)[:1500])
            traceback.print_exc()
        finally:
            _queue.task_done()


def _delivery_dir(class_id: str) -> Path:
    target = DELIVERIES / class_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def _clean_text(text: str) -> str:
    return re.sub(r"\[\d+\]", "", text or "").strip()


def _pending_section(pending_text: str) -> list[str]:
    header = "Nota importante — pendientes mencionados en clase:"
    disclaimer = "(Detección automática a partir de la transcripción; conviene verificar esta información.)"
    if pending_text.strip():
        return [header, "", pending_text.strip(), "", disclaimer]
    return [
        header,
        "",
        "No se identificaron tareas ni pendientes mencionados en la clase. "
        "Esta detección es automática, por lo que no se garantiza haber registrado todos los compromisos.",
    ]


def _description(options: dict, research: dict, pending_text: str = "") -> str:
    lines = [
        "Material generado automáticamente a partir de la transcripción de la clase.",
        "",
        "Este material fue enviado por Nachito, el asistente humanoide de aula. "
        "A partir de la transcripción de la sesión y de fuentes complementarias, "
        "se generan de forma automática:",
        "",
    ]
    if options.get("report"):
        lines.append("· Informe estructurado de la clase")
    if options.get("infographic"):
        lines.append("· Infografía resumida")
    if options.get("video"):
        lines.append("· Video de repaso")
    if options.get("transcript"):
        lines.append("· Transcripción original")
    lines += [""] + _pending_section(pending_text)
    fuentes = []
    if research.get("text_url"):
        fuentes.append(f"· {research.get('text_title') or 'Artículo'} — {research['text_url']}")
    if research.get("video_url"):
        fuentes.append(f"· {research.get('video_title') or 'Video'} — {research['video_url']}")
    if fuentes:
        lines += ["", "Fuentes complementarias:"] + fuentes
    lines += ["", "Material de carácter académico distribuido a través de Google Classroom."]
    return "\n".join(lines)


def _process(db, class_id: str) -> None:
    row = db.get_class(class_id)
    if not row:
        return
    if _is_cancelled(class_id) or row["status"] == "cancelled":
        return
    config = S.load()
    options = json.loads(row["options"] or "{}")
    profile = S.get_profile(config, row["profile_id"])
    nbp = profile["notebooklm_profile"]
    language = config.get("language", "es")

    db.update_class(class_id, status="running", error="")

    notebook_id = row["notebook_id"]
    if notebook_id and not nb.notebook_exists(nbp, notebook_id):
        notebook_id = ""
        db.update_class(class_id, notebook_id="", notebook_source_id="")
    if not notebook_id:
        notebook_id = nb.find_or_create_notebook(nbp, row["title"])
        db.update_class(class_id, notebook_id=notebook_id)

    source_id = row["notebook_source_id"]
    if not source_id:
        db.update_class(class_id, status="uploading")
        source_id = nb.add_source(nbp, notebook_id, row["txt_path"])
        db.update_class(class_id, notebook_source_id=source_id)

    research = {"text_url": "", "text_title": "", "video_url": "", "video_title": ""}
    if _is_cancelled(class_id):
        db.update_class(class_id, status="cancelled")
        return
    db.update_class(class_id, status="researching", research_status="running",
                    research_text_url="", research_text_title="",
                    research_video_url="", research_video_title="")
    try:
        text_src, video_src = academic_research.discover(
            nbp, notebook_id, source_id, cancelled=lambda: _is_cancelled(class_id))
        if text_src:
            research["text_url"], research["text_title"] = text_src["url"], text_src["title"]
            try:
                nb.add_url_source(nbp, notebook_id, text_src["url"])
            except Exception:  # noqa: BLE001
                pass
        if video_src:
            research["video_url"], research["video_title"] = video_src["url"], video_src["title"]
            try:
                nb.add_url_source(nbp, notebook_id, video_src["url"])
            except Exception:  # noqa: BLE001
                pass
        db.update_class(
            class_id,
            research_status="ready" if text_src and video_src else "partial" if text_src or video_src else "not_found",
            research_text_url=research["text_url"],
            research_text_title=research["text_title"],
            research_video_url=research["video_url"],
            research_video_title=research["video_title"],
        )
    except Exception as error:  # noqa: BLE001
        db.update_class(class_id, research_status="failed", error=str(error)[:1500])

    pending_text = ""
    if _is_cancelled(class_id):
        db.update_class(class_id, status="cancelled")
        return
    db.update_class(class_id, status="analyzing", pending_status="running")
    try:
        prompt = (
            "De la transcripción de la clase, extrae únicamente las tareas, trabajos, lecturas, "
            "entregas, fechas o compromisos mencionados para las próximas clases. Devuelve una "
            "lista con viñetas; en cada punto indica la acción y, si se mencionó, la fecha o el "
            "plazo tal como se dijo en la transcripción. No inventes información ni fechas. "
            "Si no encuentras ninguno, responde exactamente: SIN_PENDIENTES"
        )
        answer = _clean_text(nb.ask_notebook(nbp, notebook_id, prompt, source_id=source_id))
        if answer.strip().upper().startswith("SIN_PENDIENTES"):
            answer = ""
        pending_text = answer
        db.update_class(class_id, pending_status="ready", pending_text=pending_text)
    except Exception as error:  # noqa: BLE001
        db.update_class(class_id, pending_status="failed", error=str(error)[:1500])

    db.update_class(class_id, status="generating")
    delivery = _delivery_dir(class_id)
    local_files: dict[str, Path] = {}
    failures = []

    for kind, (status_field, path_field, _drive_field, filename) in KINDS.items():
        if _is_cancelled(class_id):
            db.update_class(class_id, status="cancelled")
            return
        if not options.get(kind):
            db.update_class(class_id, **{status_field: "skipped"})
            continue
        db.update_class(class_id, **{status_field: "generating"})
        try:
            timeout = int(config.get(f"{kind}_timeout", 900))
            nb.generate(nbp, notebook_id, kind, language, timeout)
            output = delivery / filename
            nb.download(nbp, notebook_id, kind, output)
            db.update_class(class_id, **{status_field: "ready", path_field: str(output)})
            local_files[kind] = output
        except Exception as error:  # noqa: BLE001
            if _is_cancelled(class_id):
                db.update_class(class_id, status="cancelled")
                return
            failures.append(f"{kind}: {error}")
            db.update_class(class_id, **{status_field: "failed", "error": str(error)[:1500]})

    transcript_path = Path(row["txt_path"]) if options.get("transcript") else None

    if not local_files and not transcript_path:
        raise RuntimeError("No se generó ningún artefacto. " + " | ".join(failures))

    if _is_cancelled(class_id):
        db.update_class(class_id, status="cancelled")
        return

    config = S.load()  # recargar por si cambió
    creds = get_credentials(S.token_path(config, profile), S.client_secret_path(config), interactive=False)
    drive = drive_service(creds)
    classroom = classroom_service(creds)

    course_id = row["classroom_course_id"]
    course_name = course_id
    if course_id:
        try:
            course_name = (
                classroom.courses().get(id=course_id, fields="name").execute()["name"]
            )
        except Exception:  # noqa: BLE001
            pass

    root_id = drive_client.find_or_create_folder(drive, config["drive_root"])
    course_folder = drive_client.find_or_create_folder(drive, course_name or "Sin curso", root_id)
    class_folder = drive_client.find_or_create_folder(
        drive, row["txt_name"] or row["title"], course_folder
    )
    db.update_class(class_id, drive_folder_id=class_folder)

    file_ids: list[str] = []
    for kind, path in local_files.items():
        info = drive_client.upload(drive, class_folder, path)
        db.update_class(class_id, **{KINDS[kind][2]: info["id"]})
        file_ids.append(info["id"])
    if transcript_path:
        info = drive_client.upload(drive, class_folder, transcript_path)
        db.update_class(class_id, transcript_drive_id=info["id"])
        file_ids.append(info["id"])

    if _is_cancelled(class_id):
        db.update_class(class_id, status="cancelled")
        return

    if course_id and file_ids:
        db.update_class(class_id, classroom_status="publishing")
        topic_id = row["topic_id"] or classroom_client.find_or_create_topic(
            classroom, course_id, config["default_topic"]
        )
        extras = []
        if research["text_url"]:
            extras.append(
                classroom_client.link_material(
                    research["text_url"], research["text_title"] or "Fuente complementaria"
                )
            )
        if research["video_url"]:
            extras.append(
                classroom_client.youtube_material(
                    research["video_url"], research["video_title"] or "Video complementario"
                )
            )
        material = classroom_client.upsert_material(
            classroom,
            course_id,
            row["classroom_material_id"] or None,
            title=row["title"],
            description=_description(options, research, pending_text),
            topic_id=topic_id,
            file_ids=file_ids,
            published=bool(options.get("publish")),
            extra_materials=extras,
        )
        db.update_class(
            class_id,
            classroom_material_id=material.get("id", ""),
            topic_id=topic_id,
            classroom_status="ready",
        )
    else:
        db.update_class(class_id, classroom_status="skipped")

    db.update_class(class_id, status="partial" if failures else "done")
