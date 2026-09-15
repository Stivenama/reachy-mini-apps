"""Subida de archivos y carpetas a Google Drive (permiso drive.file)."""

from __future__ import annotations

from pathlib import Path

from googleapiclient.http import MediaFileUpload

MIME = {
    ".md": "text/markdown",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


def find_or_create_folder(service, name: str, parent: str | None = None) -> str:
    safe = name.replace("'", "\\'")
    query = (
        f"name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    if parent:
        query += f" and '{parent}' in parents"
    items = (
        service.files()
        .list(q=query, fields="files(id,name)", spaces="drive")
        .execute()
        .get("files", [])
    )
    if items:
        return items[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        meta["parents"] = [parent]
    return service.files().create(body=meta, fields="id").execute()["id"]


def upload(service, folder_id: str, path: Path) -> dict:
    media = MediaFileUpload(
        str(path), mimetype=MIME.get(path.suffix.lower(), "application/octet-stream"), resumable=True
    )
    body = {"name": path.name, "parents": [folder_id]}
    return (
        service.files()
        .create(body=body, media_body=media, fields="id,name,webViewLink")
        .execute()
    )
