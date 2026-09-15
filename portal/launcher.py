"""Arranque del Mi Reachy Portal (uvicorn)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portal.daemon import app  # noqa: E402


def main() -> None:
    import uvicorn

    port = int(os.getenv("MI_PORTAL_PORT", "8090"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()