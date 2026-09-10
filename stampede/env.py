"""Minimal .env loader. Values are never printed or logged."""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_loaded = False


def load_dotenv(path: Path | None = None) -> None:
    global _loaded
    if _loaded:
        return
    p = path or (ROOT / ".env")
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _loaded = True


def env(name: str, default: str | None = None) -> str | None:
    load_dotenv()
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def redact(text: str) -> str:
    """Strip provider keys/tokens from URLs and messages before they reach logs or reports."""
    text = re.sub(r"(/v2/)[A-Za-z0-9_\-]{8,}", r"\1***", text or "")
    for name in ("ALCHEMY_KEY", "HYPERSYNC_TOKEN"):
        v = os.environ.get(name)
        if v and len(v) >= 8:
            text = text.replace(v, "***")
    return text


def db_path() -> Path:
    p = env("STAMPEDE_DB", "data/stampede.sqlite")
    return (ROOT / p) if not Path(p).is_absolute() else Path(p)
