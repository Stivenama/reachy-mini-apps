#!/usr/bin/env bash
# Crea entornos de desarrollo; no modifica los servicios activos del robot.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REACHY_PYTHON:-python3.12}"
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Se requiere Python 3.12"'
for name in apps asr portal; do
    target="$ROOT/.venvs/$name"
    if [[ ! -x "$target/bin/python" ]]; then
        "$PYTHON" -m venv "$target"
    fi
    "$target/bin/python" -m pip install -r "$ROOT/environment/$name-requirements.txt"
done
"$ROOT/.venvs/apps/bin/python" -m pip install --no-deps --no-build-isolation \
    -e "$ROOT/apps/reachy_mini_hub" \
    -e "$ROOT/apps/reachy_mini_audio_mixer" \
    -e "$ROOT/apps/reachy_mini_classroom"
if [[ ! -e "$ROOT/.env" ]]; then
    (umask 077; cp "$ROOT/.env.example" "$ROOT/.env")
fi
printf '%s\n' 'Entornos preparados. Completar la integración descrita en docs/INSTALACION.md.'
