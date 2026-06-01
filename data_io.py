"""ステータス・メモ・戦略履歴・広告主のデータアクセス層（Supabase）。

テーブル構成（v2）:
    advertisers           広告主マスター
    media_master          媒体マスター（media_io.py が管理）
    client_media_status   status + 4 詳細 + memo を縦持ちで保持
    client_strategy       戦略履歴（追記型）

client_media_status は1行に以下のフィールドを持つ:
    status, monthly_conversions, reward_terms,
    negotiation_status, ineligible_reason, memo

memo は「AI 更新時に touch しない」設計で、UI からのみ書き込まれる。
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping

import pandas as pd
import streamlit as st

from config import CMS_VALUE_FIELDS, DEFAULT_STATUS, STRATEGY_COLUMNS
from db import get_client
from media_io import (
    get_media_keys,
    get_sid_to_key_map,
    load_media_master,
    sid_from_media_key,
)

_TABLE_CMS = "client_media_status"
_TABLE_ADV = "advertisers"
_TABLE_STR = "client_strategy"
_IDX = "advertiser_name"

# 各フィールドの欠損時 fill 値
_FIELD_FILLS: dict[str, str] = {
    "status":              DEFAULT_STATUS,
    "monthly_conversions": "",
    "reward_terms":        "",
    "negotiation_status":  "",
    "ineligible_reason":   "",
    "memo":                "",
}


# ── 広告主マスター ────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _advertisers_cached() -> list[str]:
    resp = get_client().table(_TABLE_ADV).select("name").order("name").execute()
    return [r["name"] for r in (resp.data or [])]


def list_advertisers() -> list[str]:
    """advertisers テーブル + 他テーブルに存在する広告主名の union。"""
    advs: set[str] = set(_advertisers_cached())
    long = load_client_media_long()
    if not long.empty:
        advs.update(long[_IDX].astype(str).tolist())
    strat = load_strategy()
    if not strat.empty:
        advs.update(strat[_IDX].astype(str).tolist())
    advs.discard("")
    advs.discard("nan")
    return sorted(advs)


def ensure_advertiser(advertiser: str) -> None:
    advertiser = str(advertiser).strip()
    if not advertiser:
        return
    get_client().table(_TABLE_ADV).upsert(
        {"name": advertiser}, on_conflict="name"
    ).execute()
    _advertisers_cached.clear()


# ── client_media_status の縦持ちロード ────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_client_media_long() -> pd.DataFrame:
    """client_media_status を縦持ちで返す。"""
    cols = ["advertiser_name", "media_sid"] + CMS_VALUE_FIELDS
    resp = get_client().table(_TABLE_CMS).select(",".join(cols)).execute()
    rows = resp.data or []
    df = pd.DataFrame(rows, columns=cols)
    return df.fillna("").astype(str)


def get_cell_values(advertiser: str) -> dict[str, dict[str, str]]:
    """指定広告主の {media_key: {field: value}} を返す。

    存在しない (advertiser, media) 組はデフォルト値で埋める想定で UI 側でハンドル。
    """
    long = load_client_media_long()
    sid_map = get_sid_to_key_map()
    result: dict[str, dict[str, str]] = {}
    if long.empty:
        return result
    target = long[long[_IDX] == advertiser]
    for _, row in target.iterrows():
        mk = sid_map.get(row["media_sid"])
        if mk is None:
            continue
        result[mk] = {f: str(row.get(f, "")) for f in CMS_VALUE_FIELDS}
    return result


# ── wide 整形（画面表示用） ────────────────────────────────────────────────

def _long_to_wide_field(
    long_df: pd.DataFrame,
    field: str,
    advertisers: list[str],
    media_keys: list[str],
    sid_map: dict[str, str],
    fill: str,
) -> pd.DataFrame:
    """1 フィールド分の縦持ち → 横持ち変換。"""
    if not advertisers or not media_keys:
        df = pd.DataFrame(fill, index=advertisers or [], columns=media_keys or [])
        df.index.name = _IDX
        return df

    if long_df.empty or field not in long_df.columns:
        df = pd.DataFrame(fill, index=advertisers, columns=media_keys)
        df.index.name = _IDX
        return df

    work = long_df.copy()
    work["media_key"] = work["media_sid"].map(sid_map)
    work = work.dropna(subset=["media_key"])

    if work.empty:
        df = pd.DataFrame(fill, index=advertisers, columns=media_keys)
        df.index.name = _IDX
        return df

    wide = work.pivot_table(
        index=_IDX, columns="media_key", values=field, aggfunc="first"
    )
    wide = wide.reindex(index=advertisers, columns=media_keys, fill_value=fill)
    wide = wide.fillna(fill).astype(str)
    wide.index.name = _IDX
    return wide


def load_field_wide(field: str) -> pd.DataFrame:
    """任意フィールドの横持ち DataFrame を返す。"""
    fill = _FIELD_FILLS.get(field, "")
    return _long_to_wide_field(
        load_client_media_long(), field,
        list_advertisers(), get_media_keys(), get_sid_to_key_map(), fill,
    )


def load_status() -> pd.DataFrame:
    return load_field_wide("status")


def load_memo() -> pd.DataFrame:
    return load_field_wide("memo")


def load_notes() -> pd.DataFrame:
    """旧 API 互換。memo を返す。"""
    return load_memo()


# ── セル更新 ─────────────────────────────────────────────────────────────────

def update_cells(
    advertiser: str,
    updates: Iterable[tuple[str, Mapping[str, object]]],
) -> None:
    """(media_key, field_dict) 形式で複数セルを Upsert する。

    field_dict に含めたフィールドのみが更新される（Postgres ON CONFLICT DO UPDATE
    の仕様により、INSERT VALUES に含まれない列は触られない）。
    AI 反映時に memo を含めなければ、ユーザーの memo は確実に温存される。
    """
    advertiser = str(advertiser).strip()
    if not advertiser:
        return

    ensure_advertiser(advertiser)
    master_sids = set(load_media_master()["sid"].tolist())

    # supabase upsert は1リクエストに同じキー集合の dict を要求する。
    # フィールド集合ごとにバッチを分ける。
    from collections import defaultdict
    batches: dict[frozenset, list[dict]] = defaultdict(list)

    for media_key, field_dict in updates:
        sid = sid_from_media_key(media_key)
        if not sid or sid not in master_sids:
            continue
        clean = {
            k: ("" if v is None else str(v))
            for k, v in dict(field_dict).items()
            if k in CMS_VALUE_FIELDS
        }
        if not clean:
            continue
        row = {"advertiser_name": advertiser, "media_sid": sid, **clean}
        batches[frozenset(clean.keys())].append(row)

    client = get_client()
    for rows in batches.values():
        client.table(_TABLE_CMS).upsert(
            rows, on_conflict="advertiser_name,media_sid"
        ).execute()

    load_client_media_long.clear()


def remove_media_column(media_key: str) -> None:
    """指定 SID の client_media_status 行を明示削除。"""
    sid = sid_from_media_key(media_key)
    if not sid:
        return
    get_client().table(_TABLE_CMS).delete().eq("media_sid", sid).execute()
    load_client_media_long.clear()


# ── 戦略履歴 ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_strategy() -> pd.DataFrame:
    resp = (
        get_client()
        .table(_TABLE_STR)
        .select(",".join(STRATEGY_COLUMNS))
        .order("date")
        .execute()
    )
    rows = resp.data or []
    df = pd.DataFrame(rows, columns=STRATEGY_COLUMNS)
    for col in STRATEGY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[STRATEGY_COLUMNS].fillna("").astype(str)


def append_strategy(row: dict) -> None:
    advertiser = str(row.get("advertiser_name", "")).strip()
    if advertiser:
        ensure_advertiser(advertiser)
    safe = {col: str(row.get(col, "")) for col in STRATEGY_COLUMNS}
    if not safe["date"]:
        safe["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    get_client().table(_TABLE_STR).insert(safe).execute()
    load_strategy.clear()


def latest_strategy(advertiser: str) -> dict:
    df = load_strategy()
    if df.empty:
        return {}
    target = df[df[_IDX].astype(str) == str(advertiser)].copy()
    if target.empty:
        return {}
    target["_dt"] = pd.to_datetime(target["date"], errors="coerce")
    return target.sort_values("_dt", kind="stable").iloc[-1].drop("_dt").to_dict()


def history_strategy(advertiser: str) -> pd.DataFrame:
    df = load_strategy()
    if df.empty:
        return df
    target = df[df[_IDX].astype(str) == str(advertiser)].copy()
    if target.empty:
        return target
    target["_dt"] = pd.to_datetime(target["date"], errors="coerce")
    return (
        target.sort_values("_dt", ascending=False, kind="stable")
        .drop(columns=["_dt"])
        .reset_index(drop=True)
    )


# ── キャッシュ一括クリア ─────────────────────────────────────────────────────

def clear_all_caches() -> None:
    load_client_media_long.clear()
    load_strategy.clear()
    _advertisers_cached.clear()
    load_media_master.clear()
