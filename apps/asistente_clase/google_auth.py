"""Autenticación OAuth de Google y servicios de Drive y Classroom.

Permite varios perfiles/cuentas: cada uno guarda su propio token local.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
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
    force_reauth: bool = False,
    login_hint: str | None = None,
) -> Credentials:
    creds = None
    save_required = False
    if token_file.exists() and not force_reauth:
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except (ValueError, KeyError):
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_required = True
        except RefreshError as error:
            if not interactive:
                raise RuntimeError('La sesión de Google caducó o fue revocada. Pulsa «Conectar Classroom / Drive».') from error
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError(
                'No hay una sesión válida de Google. Pulsa «Conectar Classroom / Drive».'
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
        options = {'login_hint': login_hint} if login_hint else {}
        creds = flow.run_local_server(port=0, prompt="consent select_account",
                                      timeout_seconds=180, **options)
        if creds.granted_scopes is not None and not set(SCOPES).issubset(creds.granted_scopes):
            raise RuntimeError('Faltaron permisos de Classroom o Drive. Vuelve a conectar y acepta los permisos solicitados.')
        save_required = True
    # Persist refreshed credentials too. A cancelled login never overwrites the old file.
    if save_required:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = token_file.with_suffix('.tmp')
        temporary.write_text(creds.to_json(), encoding="utf-8")
        temporary.replace(token_file)
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
