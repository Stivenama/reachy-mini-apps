"""Autenticación OAuth de Google y servicios de Drive y Classroom.

Permite varios perfiles/cuentas: cada uno guarda su propio token local.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE = Path(__file__).resolve().parent
SECRETS = BASE / "secrets"
CLIENT_SECRET = SECRETS / "client_secret.json"
TOKEN = SECRETS / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/classroom.courses",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials",
    "https://www.googleapis.com/auth/classroom.topics",
]


def get_credentials(
    token_file: Path = TOKEN,
    client_secret: Path = CLIENT_SECRET,
    interactive: bool = True,
) -> Credentials:
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError(
                f"No hay sesión de Google válida en {token_file.name}. "
                "Ejecuta 'agregar_cuenta.py' para autorizar esta cuenta."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def classroom_service(creds: Credentials):
    return build("classroom", "v1", credentials=creds)


def drive_service(creds: Credentials):
    return build("drive", "v3", credentials=creds)


def list_teacher_courses(service) -> list[dict]:
    response = (
        service.courses()
        .list(
            teacherId="me",
            courseStates=["ACTIVE", "ARCHIVED"],
            fields="courses(id,name,section,ownerId,courseState,enrollmentCode)",
        )
        .execute()
    )
    return response.get("courses", [])


def main() -> None:
    creds = get_credentials()
    print("Autenticado. Token en:", TOKEN)
    service = classroom_service(creds)
    courses = list_teacher_courses(service)
    print(f"Cursos donde eres profesor: {len(courses)}")
    for course in courses:
        print(" -", course["name"], "| id:", course["id"], "| estado:", course.get("courseState"))


if __name__ == "__main__":
    main()
