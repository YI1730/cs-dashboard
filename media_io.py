"""媒体マスター（media_master テーブル）の CRUD + エイリアス解決。"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

import pandas as pd
import streamlit as st

from config import ALIAS_OVERRIDES, INITIAL_MEDIA, MEDIA_MASTER_COLUMNS
from db import get_client

# ── キー変換ヘルパー ─────────────────────────────────────────────────────────

def make_key(sid: str, site_name: str) -> str:
    """"{SID} - {サイト名}" 形式の媒体キーを生成。"""
    return f"{sid} - {site_name}"


def sid_from_media_key(media_key: str) -> Optional[str]:
    """媒体キーから SID を取り出す。形式不正なら None。"""
    if media_key and " - " in media_key:
        return media_key.split(" - ", 1)[0].strip()
    return None


# ── DB アクセス ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner=False)
def load_media_master() -> pd.DataFrame:
    """media_master を DataFrame で返す。aliases 列は Python list。is_active=True のみ。"""
    client = get_client()
    resp = (
        client.table("media_master")
        .select("sid, site_name, site_url, media_contact, category, aliases")
        .eq("is_active", True)
        .order("category")
        .order("sid")
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return pd.DataFrame(columns=MEDIA_MASTER_COLUMNS + ["aliases"])

    df = pd.DataFrame(rows)
    df["aliases"] = df["aliases"].apply(lambda x: x if isinstance(x, list) else [])
    for col in MEDIA_MASTER_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    for col in MEDIA_MASTER_COLUMNS:
        df[col] = df[col].fillna("").astype(str)
    return df[MEDIA_MASTER_COLUMNS + ["aliases"]].reset_index(drop=True)


def _build_record(row: dict, auto_aliases: bool = True) -> dict:
    """DB 挿入用レコードを組み立てる。aliases はマージ済みリストで渡す。"""
    sid = str(row.get("sid", "")).strip()
    site_name = str(row.get("site_name", "")).strip()
    aliases: list[str] = []
    if auto_aliases:
        aliases = _auto_aliases(site_name)
        for a in ALIAS_OVERRIDES.get(sid, []):
            n = _norm(a)
            if n and n not in aliases:
                aliases.append(n)
    else:
        raw = row.get("aliases", [])
        aliases = raw if isinstance(raw, list) else [s.strip() for s in str(raw).split(",") if s.strip()]
    return {
        "sid":           sid,
        "site_name":     site_name,
        "site_url":      str(row.get("site_url", "")).strip(),
        "media_contact": str(row.get("media_contact", "")).strip(),
        "category":      str(row.get("category", "")).strip(),
        "aliases":       list(set(aliases)),
        "is_active":     True,
    }


def save_media_master(df: pd.DataFrame) -> None:
    """DataFrame の全行を sid を競合キーとして Upsert する。aliases 列の値を尊重。"""
    client = get_client()
    records = [
        _build_record(row.to_dict(), auto_aliases=False)
        for _, row in df.iterrows()
        if str(row.get("sid", "")).strip()
    ]
    if records:
        client.table("media_master").upsert(records, on_conflict="sid").execute()
    load_media_master.clear()


def add_media(
    sid: str,
    site_name: str,
    site_url: str,
    media_contact: str,
    category: str,
) -> Optional[str]:
    """新規媒体を挿入する。SID重複時は None を返す。"""
    sid, site_name = str(sid).strip(), str(site_name).strip()
    if not sid or not site_name:
        return None
    existing = load_media_master()
    if sid in existing["sid"].values:
        return None
    get_client().table("media_master").insert(
        _build_record({
            "sid": sid, "site_name": site_name,
            "site_url": site_url, "media_contact": media_contact,
            "category": category,
        })
    ).execute()
    load_media_master.clear()
    return make_key(sid, site_name)


def delete_media(media_key: str) -> bool:
    """媒体を is_active=False に更新（ソフト削除）。FK CASCADE で client_media_status 行も連動削除。"""
    sid = sid_from_media_key(media_key)
    if not sid:
        return False
    resp = (
        get_client()
        .table("media_master")
        .update({"is_active": False})
        .eq("sid", sid)
        .execute()
    )
    load_media_master.clear()
    return bool(resp.data)


def seed_initial_media() -> int:
    """media_master が空のとき INITIAL_MEDIA を一括挿入する。投入件数を返す。"""
    existing = load_media_master()
    if not existing.empty:
        return 0
    records = [_build_record(m) for m in INITIAL_MEDIA]
    if records:
        get_client().table("media_master").insert(records).execute()
        load_media_master.clear()
    return len(records)


def upsert_from_csv(csv_df: pd.DataFrame) -> int:
    """アップロードされた CSV から media_master へ Upsert する。

    受け付けるカラム名（日本語/英語どちらも可）:
        SID / sid, サイト名 / site_name, URL / サイトURL / site_url,
        担当者 / メディア担当 / media_contact, 区分 / AFCST_区分 / category
    """
    # ── カラム名の正規化 ──────────────────────────────────────────────────
    col_map = {
        "SID": "sid", "sid": "sid",
        "サイト名": "site_name", "site_name": "site_name",
        "URL": "site_url", "サイトURL": "site_url", "site_url": "site_url",
        "担当者": "media_contact", "メディア担当": "media_contact", "media_contact": "media_contact",
        "区分": "category", "AFCST_区分": "category", "category": "category",
    }
    df = csv_df.rename(columns={k: v for k, v in col_map.items() if k in csv_df.columns})
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in ("sid", "site_name") if c not in df.columns]
    if missing:
        raise ValueError(f"必須カラムが不足しています: {missing}  （列名: {list(csv_df.columns)}）")

    records = [
        _build_record(row.to_dict())
        for _, row in df.astype(str).iterrows()
        if str(row.get("sid", "")).strip() and str(row.get("site_name", "")).strip()
    ]
    if records:
        get_client().table("media_master").upsert(records, on_conflict="sid").execute()
        load_media_master.clear()
    return len(records)


# ── 派生ビュー（キャッシュ不要・master キャッシュに乗る） ──────────────────

def get_media_keys() -> list[str]:
    df = load_media_master()
    return [make_key(r["sid"], r["site_name"]) for _, r in df.iterrows()]


def get_sid_to_key_map() -> dict[str, str]:
    df = load_media_master()
    return {r["sid"]: make_key(r["sid"], r["site_name"]) for _, r in df.iterrows()}


def get_media_contacts() -> dict[str, str]:
    df = load_media_master()
    return {make_key(r["sid"], r["site_name"]): r["media_contact"] for _, r in df.iterrows()}


def get_category_map() -> dict[str, list[str]]:
    """カテゴリ → [media_key, ...] の辞書を返す。"""
    df = load_media_master()
    result: dict[str, list[str]] = {}
    for _, r in df.iterrows():
        result.setdefault(r["category"] or "未分類", []).append(
            make_key(r["sid"], r["site_name"])
        )
    return result


# ── エイリアス解決用 ─────────────────────────────────────────────────────────

def _norm(text: object) -> str:
    return unicodedata.normalize("NFKC", str(text)).lower().strip()


# 業務メタタグとして無視する 【】 の中身（小文字正規化済み）
_META_BRACKETS: set[str] = {"ls", "上位層", "新規", "休止", "終了", "保留"}


def _auto_aliases(site_name: str) -> list[str]:
    """サイト名から表記ゆれエイリアスを自動生成する。

    ロジック:
        1. NFKC + lower した本体名
        2. 【...】 を除去した版
        3. （...） / (...) を除去した版（最も短い名前）
        4. 【ブランド名】 のブランド名（LS/上位層 などのメタタグは除外）
        5. （旧:○○） の "○○" 部分（旧名・改称前の名前）

    例:
        "EPOSポイントUPサイト（旧:たまるマーケット）"
            → ['epossポイントupサイト(旧:たまるマーケット)',
               'epossポイントupサイト',
               'たまるマーケット']

        "イーウェル【WELBOX】"
            → ['イーウェル【welbox】', 'イーウェル', 'welbox']

        "モッピーEC（定率）【LS】"
            → ['モッピーec(定率)【ls】', 'モッピーec(定率)', 'モッピーec']
            ※ 【LS】 はメタタグとして除外、（定率）は媒体名に近いので保持
    """
    aliases: set[str] = set()
    n = _norm(site_name)
    aliases.add(n)

    # ── 1. 【】 内のブランド名を抽出（メタタグは除外） ──────────────────────
    for bc in re.findall(r"【([^】]*)】", n):
        b = _norm(bc)
        if b and b not in _META_BRACKETS:
            aliases.add(b)

    # ── 2. 【...】 を除去した版 ────────────────────────────────────────────
    no_brackets = re.sub(r"【[^】]*】", "", n).strip()
    aliases.add(no_brackets)

    # ── 3. （...） / (...) 内の "旧:○○" パターン抽出 ─────────────────────
    # NFKC で 全角() → 半角() に変換されているので () でマッチ
    for pc in re.findall(r"\(([^)]*)\)", no_brackets):
        p = pc.strip()
        m = re.match(r"^旧[:：]?\s*(.+)$", p)
        if m:
            old = _norm(m.group(1))
            if old:
                aliases.add(old)

    # ── 4. （...） / (...) を除去した版（最も短い名前） ────────────────────
    no_parens = re.sub(r"\([^)]*\)", "", no_brackets).strip()
    aliases.add(no_parens)

    return [a for a in aliases if a]


def upsert_from_excel(file_obj, sheet_name: str = "01.エッセンシャル媒体") -> int:
    """Excel から エッセンシャル媒体シートを読んで Upsert する。

    既定: 「01.エッセンシャル媒体」シート、3行目をヘッダーとして読み込む。
    必須カラム: SID, サイト名（受け付ける表記ゆれは upsert_from_csv と同じ）。
    """
    df = pd.read_excel(
        file_obj,
        sheet_name=sheet_name,
        header=2,        # r3 がヘッダー（0-indexed では 2）
        dtype=str,
        engine="openpyxl",
    )
    # 改行や空白を除去（"最低\n報酬率" などの列名対策）
    df.columns = [
        unicodedata.normalize("NFKC", str(c)).replace("\n", "").strip()
        for c in df.columns
    ]
    return upsert_from_csv(df.fillna(""))


def build_resolve_master() -> dict[str, list[str]]:
    """resolve_media_name 用の {media_key: [alias, ...]} 辞書を構築する。"""
    df = load_media_master()
    result: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        sid = str(row["sid"])
        site_name = str(row["site_name"])
        media_key = make_key(sid, site_name)

        aliases = set(_auto_aliases(site_name))
        for a in (row.get("aliases") or []):
            n = _norm(str(a))
            if n:
                aliases.add(n)
        for a in ALIAS_OVERRIDES.get(sid, []):
            n = _norm(a)
            if n:
                aliases.add(n)
        result[media_key] = list(aliases)
    return result
