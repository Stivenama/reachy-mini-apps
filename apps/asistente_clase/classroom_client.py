"""Publicación de materiales en Google Classroom (CourseWorkMaterial)."""

from __future__ import annotations

import re

_YOUTUBE_PATTERNS = (
    r"youtu\.be/([\w-]{11})",
    r"[?&]v=([\w-]{11})",
    r"youtube\.com/embed/([\w-]{11})",
    r"youtube\.com/shorts/([\w-]{11})",
)


def youtube_id(url: str) -> str:
    for pattern in _YOUTUBE_PATTERNS:
        match = re.search(pattern, url or "")
        if match:
            return match.group(1)
    return ""


def is_youtube(url: str) -> bool:
    return bool(youtube_id(url))


def list_courses(service) -> list[dict]:
    response = (
        service.courses()
        .list(
            teacherId="me",
            courseStates=["ACTIVE", "ARCHIVED"],
            fields="courses(id,name,section,courseState)",
        )
        .execute()
    )
    return response.get("courses", [])


def list_topics(service, course_id: str) -> list[dict]:
    return service.courses().topics().list(courseId=course_id).execute().get("topic", [])


def find_or_create_topic(service, course_id: str, name: str) -> str:
    for topic in list_topics(service, course_id):
        if topic["name"] == name:
            return topic["topicId"]
    created = service.courses().topics().create(courseId=course_id, body={"name": name}).execute()
    return created["topicId"]


def drive_material(file_id: str) -> dict:
    return {"driveFile": {"driveFile": {"id": file_id}, "shareMode": "VIEW"}}


def link_material(url: str, title: str) -> dict:
    return {"link": {"url": url, "title": title or url}}


def youtube_material(url: str, title: str) -> dict:
    return {"youtubeVideo": {"id": youtube_id(url), "title": title or url}}


def upsert_material(
    service,
    course_id: str,
    material_id: str | None,
    title: str,
    description: str,
    topic_id: str,
    file_ids: list[str],
    published: bool,
    extra_materials: list[dict] | None = None,
) -> dict:
    materials_list = [drive_material(fid) for fid in file_ids]
    materials_list.extend(extra_materials or [])
    body = {
        "title": title,
        "description": description,
        "state": "PUBLISHED" if published else "DRAFT",
        "materials": materials_list,
    }
    if topic_id:
        body["topicId"] = topic_id
    materials = service.courses().courseWorkMaterials()
    if material_id:
        # El updateMask de la API no admite 'materials': borrar y crear de nuevo.
        try:
            materials.delete(courseId=course_id, id=material_id).execute()
        except Exception:  # noqa: BLE001
            pass
    return materials.create(courseId=course_id, body=body).execute()
