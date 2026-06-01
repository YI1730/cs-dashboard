"""共通ユーティリティ（原子ファイル書き込みなど）。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd

from config import DATA_DIR


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_csv(df: pd.DataFrame, path: Path, *, index: bool) -> None:
    """同一ディレクトリに一時ファイルを書いてから rename（原子的置換）。"""
    ensure_data_dir()
    path = Path(path)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.stem + ".", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    try:
        df.to_csv(tmp_path, index=index, encoding="utf-8-sig")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
