"""媒体名の表記ゆれ吸収（Alias解決）ロジック。

優先順位:
    1. 入力テキストに含まれる SID（6〜8桁数字）が master のキーと一致するなら即決
    2. 各 alias の最長一致
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

UNKNOWN_LABEL = "不明な媒体"

_SID_PATTERN = re.compile(r"\d{6,8}")


def _normalize(text: object) -> str:
    """全角/半角・大小文字・互換文字を NFKC + 小文字化で正規化する。"""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    return s.lower().strip()


def resolve_media_name(
    input_text: str,
    master: dict[str, list[str]] | None = None,
    fallback: Optional[str] = None,
) -> Optional[str]:
    """入力テキストから media_key（"{SID} - {サイト名}"）を特定する。

    1段目: 入力に含まれる数字列がいずれかの SID と完全一致すればそれを返す
    2段目: 各 alias の最長一致を採用

    master が None の場合は media_io.build_resolve_master() を呼び出して
    最新の媒体マスターを動的に取得する。
    """
    if master is None:
        from media_io import build_resolve_master
        master = build_resolve_master()

    norm_input = _normalize(input_text)
    if not norm_input:
        return fallback

    # ── 1段目: SID 直接マッチ ───────────────────────────────────────────────
    sids = {key.split(" - ", 1)[0] for key in master.keys() if " - " in key}
    for found in _SID_PATTERN.findall(norm_input):
        if found in sids:
            for key in master.keys():
                if key.startswith(found + " - "):
                    return key

    # ── 2段目: Alias 最長一致 ───────────────────────────────────────────────
    best_key: Optional[str] = None
    best_len = 0

    for key, aliases in master.items():
        official_name = key.split(" - ", 1)[1] if " - " in key else key
        candidates = list(aliases) + [_normalize(key), _normalize(official_name)]

        for alias in candidates:
            a = _normalize(alias)
            if a and a in norm_input and len(a) > best_len:
                best_len = len(a)
                best_key = key

    return best_key if best_key is not None else fallback
