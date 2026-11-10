"""极简 JSON 存储,带原子写入。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, rel: str) -> Path:
        path = (self.base_dir / rel).resolve()
        base = self.base_dir.resolve()
        if not str(path).startswith(str(base)):
            raise ValueError(f"非法存储路径: {rel}")
        return path

    def load(self, rel: str, default: Any = None) -> Any:
        path = self._path(rel)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, rel: str, data: Any) -> None:
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
