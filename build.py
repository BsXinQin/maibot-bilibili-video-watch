"""打发布包：dist/<插件 id>-<版本>.zip。

版本号从 ``_manifest.json`` 读，不在这里写死；压缩包顶层目录就是插件 id，
解压后直接就是 Maibot 需要的目录结构。

用法::

    python build.py
"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

# 不打进发布包的东西：本地运行时配置、缓存、临时产物
SKIP_NAMES = {
    "config.toml",
    "_testout.txt",
    ".gitignore",
    ".gitattributes",
}
SKIP_DIRS = {
    ".git",
    ".github",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".venv",
    "venv",
}


def collect(root: Path) -> list[tuple[Path, str]]:
    """收集要打包的文件，返回 (绝对路径, 包内相对路径)。"""
    items: list[tuple[Path, str]] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name in SKIP_NAMES or name.endswith((".pyc", ".pyo")):
                continue
            full = Path(base) / name
            items.append((full, full.relative_to(root).as_posix()))
    items.sort(key=lambda pair: pair[1])
    return items


def main() -> int:
    manifest = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))
    plugin_id = str(manifest["id"])
    version = str(manifest["version"])

    DIST.mkdir(exist_ok=True)
    target = DIST / f"{plugin_id}-{version}.zip"
    if target.exists():
        target.unlink()

    files = collect(ROOT)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for full, rel in files:
            zf.write(full, f"{plugin_id}/{rel}")

    size = target.stat().st_size
    print(f"打包完成：{target}")
    print(f"插件 {plugin_id} v{version}，{len(files)} 个文件，{size} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
