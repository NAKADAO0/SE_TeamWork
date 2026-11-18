"""一键启动入口：加载本地 .env（若存在）后启动完整前后端服务。"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def main() -> None:
    _load_dotenv(ROOT / ".env")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8010"))
    uvicorn.run("backend.api:app", host=host, port=port)


if __name__ == "__main__":
    main()
