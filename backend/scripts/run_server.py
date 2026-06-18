from __future__ import annotations

import sys
import os
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.security import validate_runtime_security


def main() -> None:
    settings = get_settings()
    validate_runtime_security(settings)
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        app_dir=".",
        reload=os.environ.get("OPENFLOW_RELOAD", "").lower() in {"1", "true", "yes"},
    )


if __name__ == "__main__":
    main()
