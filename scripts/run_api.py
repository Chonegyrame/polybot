"""Launch the FastAPI app + in-process scheduler.

Run from project root:
    ./venv/Scripts/python.exe scripts/run_api.py

Then visit:
    http://localhost:8000/        — root
    http://localhost:8000/docs    — interactive API explorer
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,  # reload + lifespan don't play nicely; use explicit restart
        log_level="info",
    )
