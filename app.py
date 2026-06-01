"""CS アフィリエイト媒体管理ダッシュボード（Supabase 版）。

起動:
    streamlit run app.py

前提:
    .streamlit/secrets.toml に SUPABASE_URL / SUPABASE_KEY / PASSWORD を設定
    Supabase で sql/schema.sql を実行済み
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from auth import logout_button, require_password
from config import (
    CATEGORY_CHOICES,
    CMS_FIELD_LABELS,
    CMS_VALUE_FIELDS,
    DEFAULT_STATUS,
    STATUS_CHOICES,
    STRATEGY_COLUMNS,
)
from data_io import (
    append_strategy,
    clear_all_caches,
    ensure_advertiser,
    get_cell_values,
    history_strategy,
    latest_strategy,
    list_advertisers,
    load_client_media_long,
    load_field_wide,
    load_status,
    load_strategy,
    remove_media_column,
    update_cells,
)
from media_io import (
    add_media,
    build_resolve_master,
    delete_media,
    get_category_map,
    get_media_contacts,
    get_media_keys,
    get_sid_to_key_map,
    load_media_master,
    make_key,
    save_media_master,
    seed_initial_media,
    sid_from_media_key,
    upsert_from_csv,
    upsert_from_excel,
)
from normalize import UNKNOWN_LABEL, resolve_media_name

st.set_page_config(
    page_title="CS 媒体管理ダッシュボード",
    page_icon="📊",
    layout="wide",
)


# ======== セッションステート ================================================

def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("flash", None)
    ss.setdefault("ai_raw_log", "")
    ss.setdefault("ai_extracted", None)
    ss.setdefault("selected_advertiser", None)
    ss.setdefault("master_editor_ver", 0)


def _flash(level: str, msg: str) -> None:
    st.session_state["flash"] = (level, msg)


def _show_flash() -> None:
    flash = st.session_state.pop("flash", None)
    if not flash:
        return
    getattr(st, flash[0], st.info)(flash[1])


# ======== サイドバー ========================================================

def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### ⚙️ 操作")
        if st.button("🔄 データ再読込", use_container_width=True):
            clear_all_caches()
            _flash("success", "キャッシュをクリアしました。")
            st.rerun()
        st.markdown("---")
        logout_button()


# ======== AI 抽出モック =====================================================

def _mock_extract(log_text: str) -> list[dict[str, Any]]:  # noqa: ARG001
    """本番では LLM を呼ぶ。表記ゆれ込みの固定ダミーを返す。

    各エントリは以下のキーを持ちうる（任意。memo は AI が出さない領域）:
        media_name, status,
        monthly_conversions, reward_terms,
        negotiation_status, ineligible_reason
    """
    return [
        {
            "media_name": "エポス", "status": "掲載中",
            "monthly_conversions": "120",
            "reward_terms": "1.5% (初成約のみ)",
        },
        {
            "media_name": "ANAマイル", "status": "交渉中",
            "negotiation_status": "報酬+0.3% で調整中、来週レス待ち",
        },
        {
            "media_name": "モッピー", "status": "レス待ち",
            "negotiation_status": "提案書送付済み（先週）",
        },
        {
            "media_name": "JAL", "status": "掲載不可",
            "ineligible_reason": "競合排他のため",
        },
        {
            "media_name": "謎の媒体Z", "status": "未掲載",
        },
    ]


# 抽出結果 → 表示用 DataFrame の列順
_AI_DISPLAY_COLS = [
    "入力（生テキスト）",
    "解決結果（公式キー）",
    "ステータス",
    "月件数",
    "経済条件",
    "交渉ステータス",
    "掲載不可理由",
]


def _extracted_to_df(items: list[dict[str, Any]]) -> pd.DataFrame:
    master = build_resolve_master()
    rows = []
    for item in items:
        rows.append({
            "入力（生テキスト）":    item.get("media_name", ""),
            "解決結果（公式キー）": resolve_media_name(
                item.get("media_name", ""), master=master, fallback=UNKNOWN_LABEL
            ),
            "ステータス":           item.get("status", DEFAULT_STATUS),
            "月件数":               item.get("monthly_conversions", ""),
            "経済条件":             item.get("reward_terms", ""),
            "交渉ステータス":         item.get("negotiation_status", ""),
            "掲載不可理由":          item.get("ineligible_reason", ""),
        })
    df = pd.DataFrame(rows, columns=_AI_DISPLAY_COLS)
    return df


# ======== タブ① クライアント個別管理 ========================================

def render_client_tab() -> None:
    st.subheader("クライアント個別管理")

    advertisers = list_advertisers()
    col_sel, col_new = st.columns([3, 2])

    # 新規追加直後はその広告主を選択状態にする。
    # selectbox の widget キー(adv_select_box)は「生成後」に session_state で
    # 書き換えると StreamlitAPIException になるため、非 widget キー
    # (_pending_advertiser)で受け渡し、widget 生成「前」のここで反映する。
    pending = st.session_state.pop("_pending_advertiser", None)
    if pending and pending in advertisers:
        st.session_state["adv_select_box"] = pending

    # 保持値が候補に無い場合（削除済み広告主など）は先頭へリセット。
    # この代入も selectbox 生成「前」なので安全。
    if advertisers and st.session_state.get("adv_select_box") not in advertisers:
        st.session_state["adv_select_box"] = advertisers[0]

    with col_sel:
        if advertisers:
            selected = st.selectbox("広告主を選択", advertisers, key="adv_select_box")
        else:
            st.info("広告主データがありません。右側から新規追加してください。")
            selected = None

    with col_new:
        with st.form("frm_add_adv", clear_on_submit=True):
            new_adv = st.text_input("新規広告主を追加", placeholder="例: I社（スポーツ用品）")
            if st.form_submit_button("追加"):
                new_adv = new_adv.strip()
                if not new_adv:
                    _flash("warning", "広告主名を入力してください。")
                else:
                    ensure_advertiser(new_adv)
                    # widget 生成後なので adv_select_box は直接触らない。
                    # 非 widget キーに退避し、次回 run の生成前に反映する。
                    st.session_state["_pending_advertiser"] = new_adv
                    _flash("success", f"「{new_adv}」を追加しました。")
                st.rerun()

    # 他タブ互換のため同期（selected_advertiser は非 widget キーなので安全）。
    st.session_state["selected_advertiser"] = selected
    if not selected:
        return

    ensure_advertiser(selected)
    st.markdown("---")

    # ── 戦略パネル ──────────────────────────────────────────────────────────
    st.markdown("### 📝 戦略パネル")
    latest = latest_strategy(selected)

    with st.form(f"frm_strategy_{selected}"):
        c1, c2 = st.columns(2)
        with c1:
            focus_theme        = st.text_input("注力テーマ",        value=latest.get("focus_theme", ""))
            base_commission    = st.text_input("ベース報酬 (%)",    value=latest.get("base_commission", ""))
        with c2:
            pending_issue      = st.text_area( "課題 / ペンディング", value=latest.get("pending_issue", ""), height=90)
            negotiable_commission = st.text_input("交渉可能報酬 (%)", value=latest.get("negotiable_commission", ""))
        topic_summary = st.text_area("打合せ / トピック概要", value=latest.get("topic_summary", ""), height=90)

        if st.form_submit_button("戦略を保存（履歴追記）", type="primary"):
            append_strategy({
                "date":                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "advertiser_name":       selected,
                "topic_summary":         topic_summary,
                "focus_theme":           focus_theme,
                "pending_issue":         pending_issue,
                "base_commission":       base_commission,
                "negotiable_commission": negotiable_commission,
            })
            _flash("success", f"「{selected}」の戦略を履歴に追記しました。")
            st.rerun()

    with st.expander("📚 過去の戦略履歴", expanded=False):
        hist = history_strategy(selected)
        if hist.empty:
            st.caption("履歴はまだありません。")
        else:
            st.dataframe(hist[STRATEGY_COLUMNS], use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── 媒体更新パネル ─────────────────────────────────────────────────────
    st.markdown("### 📡 媒体更新パネル")
    master_df = load_media_master()
    if master_df.empty:
        st.warning("媒体マスターが空です。「④ 媒体マスター管理」タブからデータを投入してください。")
        return

    contacts  = get_media_contacts()
    cat_map   = get_category_map()
    media_set = set(get_media_keys())
    cur_all   = get_cell_values(selected)  # {media_key: {field: value}}

    # フィルタ: ステータスでカテゴリを絞る
    flt_col1, flt_col2 = st.columns([2, 3])
    with flt_col1:
        status_filter = st.multiselect(
            "ステータスで絞り込み",
            STATUS_CHOICES,
            default=[],
            key=f"flt_status_{selected}",
            help="未選択なら全媒体を表示",
        )
    with flt_col2:
        st.caption(
            "💡 **保存ルール**: ステータス／月件数／経済条件／交渉メモ／不可理由 は AI 抽出で上書きされます。"
            "**メモ** だけは AI 更新で温存される自由記述領域です。"
        )

    with st.form(f"frm_media_{selected}"):
        widget: dict[str, dict[str, str]] = {}
        rendered_count = 0

        for category, keys_in_cat in cat_map.items():
            valid_keys = [k for k in keys_in_cat if k in media_set]
            if not valid_keys:
                continue
            # フィルタ適用
            if status_filter:
                valid_keys = [
                    k for k in valid_keys
                    if cur_all.get(k, {}).get("status", DEFAULT_STATUS) in status_filter
                ]
                if not valid_keys:
                    continue

            with st.expander(
                f"**{category}** ({len(valid_keys)} 媒体)",
                expanded=(category == "主要還元系" or status_filter),
            ):
                for mk in valid_keys:
                    cur = cur_all.get(mk, {})
                    cur_st = cur.get("status", DEFAULT_STATUS)
                    if cur_st not in STATUS_CHOICES:
                        cur_st = DEFAULT_STATUS

                    label   = mk.split(" - ", 1)[1] if " - " in mk else mk
                    contact = contacts.get(mk, "")

                    # ── Row 1: 媒体名 + ステータス + 月件数 + 経済条件 ─────
                    r1 = st.columns([3, 2, 1, 3])
                    r1[0].markdown(
                        f"**{label}**  \n<small>👤 {contact}</small>",
                        unsafe_allow_html=True,
                    )
                    new_st = r1[1].selectbox(
                        "ステータス",
                        STATUS_CHOICES,
                        index=STATUS_CHOICES.index(cur_st),
                        key=f"s_{selected}_{mk}",
                        label_visibility="collapsed",
                    )
                    new_mc = r1[2].text_input(
                        "月件数",
                        value=cur.get("monthly_conversions", ""),
                        key=f"mc_{selected}_{mk}",
                        placeholder="月件数",
                        label_visibility="collapsed",
                    )
                    new_rt = r1[3].text_input(
                        "経済条件",
                        value=cur.get("reward_terms", ""),
                        key=f"rt_{selected}_{mk}",
                        placeholder="経済条件 (例: 1.5% / 初成約のみ)",
                        label_visibility="collapsed",
                    )

                    # ── Row 2: 交渉メモ + 不可理由 + 保護メモ ───────────────
                    r2 = st.columns([3, 3, 3])
                    new_ns = r2[0].text_input(
                        "交渉ステータス",
                        value=cur.get("negotiation_status", ""),
                        key=f"ns_{selected}_{mk}",
                        placeholder="交渉ステータス（交渉中／レス待ち時）",
                        label_visibility="collapsed",
                    )
                    new_ir = r2[1].text_input(
                        "掲載不可理由",
                        value=cur.get("ineligible_reason", ""),
                        key=f"ir_{selected}_{mk}",
                        placeholder="掲載不可理由",
                        label_visibility="collapsed",
                    )
                    new_mm = r2[2].text_input(
                        "メモ",
                        value=cur.get("memo", ""),
                        key=f"mm_{selected}_{mk}",
                        placeholder="📝 メモ（AI 更新で保護）",
                        label_visibility="collapsed",
                    )

                    st.markdown(
                        "<hr style='margin:4px 0; border:none; border-top:1px solid #eee;'/>",
                        unsafe_allow_html=True,
                    )
                    widget[mk] = {
                        "status":              new_st,
                        "monthly_conversions": new_mc,
                        "reward_terms":        new_rt,
                        "negotiation_status":  new_ns,
                        "ineligible_reason":   new_ir,
                        "memo":                new_mm,
                    }
                    rendered_count += 1

        if rendered_count == 0:
            st.info("条件に合致する媒体がありません。フィルタを変更してください。")

        if st.form_submit_button(
            f"📥 {rendered_count} 件の媒体状態をまとめて保存", type="primary"
        ):
            if rendered_count == 0:
                _flash("warning", "保存対象がありません。")
            else:
                updates = [(mk, fields) for mk, fields in widget.items()]
                update_cells(selected, updates)
                _flash("success", f"「{selected}」の媒体状態を保存しました（{rendered_count} 件）。")
            st.rerun()


# ======== タブ② AI 抽出（モック）==========================================

def render_ai_tab() -> None:
    st.subheader("AI 抽出（モック）")
    st.caption("業務ログを貼り付けて「抽出」を押すと、固定ダミー JSON を返します。")

    advertisers = list_advertisers()
    if not advertisers:
        st.info("先にクライアント個別管理タブで広告主を追加してください。")
        return

    target = st.selectbox("反映先の広告主", advertisers, key="ai_adv")
    st.session_state["ai_raw_log"] = st.text_area(
        "業務ログ", value=st.session_state["ai_raw_log"], height=160,
        placeholder="例: 本日エポスの担当とMTG。3/15から掲載開始で合意。ANAマイルは交渉中...",
    )

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("抽出する", type="primary", use_container_width=True):
            items = _mock_extract(st.session_state["ai_raw_log"])
            st.session_state["ai_extracted"] = _extracted_to_df(items)
            _flash("info", f"{len(items)} 件を抽出しました。")
            st.rerun()
    with col_b:
        if st.button("クリア", use_container_width=True):
            st.session_state["ai_extracted"] = None
            st.rerun()

    extracted: pd.DataFrame | None = st.session_state.get("ai_extracted")
    if extracted is None or extracted.empty:
        st.caption("抽出結果はまだありません。")
        return

    st.markdown("#### 抽出結果プレビュー")
    st.dataframe(extracted, use_container_width=True, hide_index=True)

    bad  = extracted["解決結果（公式キー）"].isin([None, UNKNOWN_LABEL])
    n_ok = int((~bad).sum())
    n_ng = int(bad.sum())
    if n_ng:
        st.warning(f"マッピング不可 {n_ng} 件（反映時にスキップ）")

    st.caption(
        "ℹ️ 反映時に書き換わるのは ステータス/月件数/経済条件/交渉ステータス/掲載不可理由 の **5フィールド** のみ。"
        "ユーザーの **メモ** 列は touch しません。"
    )

    if st.button(f"反映する（解決済 {n_ok} 件 → {target}）", type="primary", disabled=(n_ok == 0)):
        ups: list[tuple[str, dict[str, str]]] = []
        for _, row in extracted.iterrows():
            mk = row["解決結果（公式キー）"]
            if mk in (None, UNKNOWN_LABEL):
                continue
            # memo は含めない（保護領域）
            fields = {
                "status":              str(row["ステータス"]),
                "monthly_conversions": str(row["月件数"]),
                "reward_terms":        str(row["経済条件"]),
                "negotiation_status":  str(row["交渉ステータス"]),
                "ineligible_reason":   str(row["掲載不可理由"]),
            }
            # 空文字フィールドは含めない（既存値を温存するため）
            fields = {k: v for k, v in fields.items() if v.strip() or k == "status"}
            ups.append((str(mk), fields))

        update_cells(target, ups)
        st.session_state["ai_extracted"] = None
        _flash(
            "success",
            f"{len(ups)} 件を「{target}」に反映しました（スキップ {n_ng} 件 / メモ列は保護）。",
        )
        st.rerun()


# ======== タブ③ 全体進捗一覧（星取表）=====================================

def _format_overview_cell(
    status: str, mc: str, rt: str, ns: str, ir: str, memo: str
) -> str:
    """星取表 1 セル分の表示文字列を組み立てる。

    レイアウト（status により下段が変わる）:
        掲載中:
            掲載中
            月100件 / 1.5%
            【ユーザーメモ】

        交渉中 / レス待ち:
            交渉中
            報酬+0.3%で調整中
            【ユーザーメモ】

        掲載不可:
            掲載不可
            競合排他のため
            【ユーザーメモ】

        未掲載:
            未掲載
            【ユーザーメモ】（あれば）
    """
    parts = [status if status else DEFAULT_STATUS]

    if status == "掲載中":
        details = []
        if mc.strip():
            details.append(f"月{mc.strip()}件")
        if rt.strip():
            details.append(rt.strip())
        if details:
            parts.append(" / ".join(details))
    elif status in ("交渉中", "レス待ち"):
        if ns.strip():
            parts.append(ns.strip())
    elif status == "掲載不可":
        if ir.strip():
            parts.append(ir.strip())

    text = "\n".join(parts)
    if memo.strip():
        text += f"\n【📝 {memo.strip()}】"
    return text


def render_overview_tab() -> None:
    st.subheader("全体進捗一覧（星取表）")

    long = load_client_media_long()
    advs = list_advertisers()
    media_keys = get_media_keys()
    sid_map = get_sid_to_key_map()

    if not advs or not media_keys:
        st.info("データがありません。「④ 媒体マスター管理」タブから初期データを投入するか、広告主を追加してください。")
        return

    # ── 1セル = 全フィールド結合の DataFrame を構築 ────────────────────────
    combined = pd.DataFrame(DEFAULT_STATUS, index=advs, columns=media_keys, dtype=str)
    if not long.empty:
        for _, row in long.iterrows():
            adv = str(row["advertiser_name"])
            mk = sid_map.get(str(row["media_sid"]))
            if adv not in combined.index or not mk or mk not in combined.columns:
                continue
            combined.at[adv, mk] = _format_overview_cell(
                str(row.get("status", DEFAULT_STATUS)),
                str(row.get("monthly_conversions", "")),
                str(row.get("reward_terms", "")),
                str(row.get("negotiation_status", "")),
                str(row.get("ineligible_reason", "")),
                str(row.get("memo", "")),
            )
    combined.index.name = "advertiser_name"

    # ── ステータスサマリ（KPI） ──────────────────────────────────────────────
    if not long.empty:
        st.markdown("##### 📊 ステータスサマリ")
        status_counts = long["status"].value_counts().reindex(STATUS_CHOICES, fill_value=0)
        kpi_cols = st.columns(len(STATUS_CHOICES))
        for i, st_label in enumerate(STATUS_CHOICES):
            kpi_cols[i].metric(st_label, int(status_counts.get(st_label, 0)))

    st.caption(
        f"広告主: {len(advs)} / 媒体: {len(media_keys)}　"
        "— セル: 1行目=ステータス／2行目=詳細(掲載中:月件数・経済条件、交渉中/レス待ち:交渉ST、掲載不可:理由)／"
        "末尾【📝 ...】=ユーザーメモ"
    )
    st.dataframe(
        combined,
        use_container_width=True,
        height=min(120 + 60 * len(advs), 900),
    )


# ======== タブ④ 媒体マスター管理 ==========================================

def render_master_tab() -> None:
    st.subheader("媒体マスター管理")
    master_df = load_media_master()

    # ── A. ファイルから一括登録（Excel / CSV / TSV） ─────────────────────
    st.markdown("### 📤 ファイルから一括登録（Upsert）")
    st.caption(
        "**Excel**（.xlsx）、**CSV**、**TSV** に対応。"
        "Excel の場合は既定で「01.エッセンシャル媒体」シートを読み込みます（3行目がヘッダー想定）。"
        "必須列は **SID** と **サイト名** のみ。"
    )

    with st.expander("📋 受け付けるカラム名（日本語・英語どちらも自動マッピング）", expanded=False):
        st.markdown("""
| 項目 | 受け付ける列名 |
|---|---|
| SID | `SID` / `sid` |
| サイト名 | `サイト名` / `site_name` |
| URL | `URL` / `サイトURL` / `site_url` |
| 担当者 | `担当者` / `メディア担当` / `media_contact` |
| 区分 | `区分` / `AFCST_区分` / `category` |
        """)

    uploaded = st.file_uploader(
        "Excel / CSV / TSV ファイルをドロップまたは選択",
        type=["xlsx", "xls", "csv", "tsv"],
        key="file_upload",
    )

    if uploaded is not None:
        ext = uploaded.name.lower().rsplit(".", 1)[-1]
        try:
            if ext in ("xlsx", "xls"):
                # Excel: シート選択（複数候補あれば）
                xl = pd.ExcelFile(uploaded, engine="openpyxl")
                default_sheet = (
                    "01.エッセンシャル媒体"
                    if "01.エッセンシャル媒体" in xl.sheet_names
                    else xl.sheet_names[0]
                )
                sheet = st.selectbox(
                    "シートを選択", xl.sheet_names,
                    index=xl.sheet_names.index(default_sheet),
                    key="excel_sheet",
                )
                header_row = st.number_input(
                    "ヘッダー行（1始まり）", min_value=1, max_value=20, value=3,
                    key="excel_header_row",
                )
                raw_df = pd.read_excel(
                    uploaded, sheet_name=sheet, header=header_row - 1,
                    dtype=str, engine="openpyxl",
                ).fillna("")
                # 列名の改行・空白を整理
                raw_df.columns = [str(c).replace("\n", "").strip() for c in raw_df.columns]
            else:
                sep = "\t" if ext == "tsv" else ","
                raw_df = pd.read_csv(uploaded, sep=sep, dtype=str).fillna("")

            # SID が数字行のみ抜粋（合計行・空行を除く）
            if "SID" in raw_df.columns or "sid" in raw_df.columns:
                sid_col = "SID" if "SID" in raw_df.columns else "sid"
                valid = raw_df[raw_df[sid_col].astype(str).str.match(r"^\d+$", na=False)]
            else:
                valid = raw_df

            st.markdown(f"**プレビュー** — 全 {len(raw_df)} 行 / 有効 {len(valid)} 行")
            st.dataframe(valid.head(15), use_container_width=True, hide_index=True)

            if st.button("この内容で Upsert する", type="primary", key="btn_upsert_file"):
                n = upsert_from_csv(valid)
                _flash("success", f"{n} 件の媒体を media_master に登録・更新しました。")
                st.rerun()

        except ValueError as e:
            st.error(f"カラムエラー: {e}")
        except Exception as e:
            st.error(f"ファイル読み込みエラー: {e}")

    st.markdown("---")

    # ── B. 初期シード ──────────────────────────────────────────────────────
    if master_df.empty:
        st.warning("媒体マスターが空です。初期データ（67 媒体）を一括投入できます。")
        if st.button("初期媒体データをシードする", type="primary", key="btn_seed"):
            n = seed_initial_media()
            _flash("success", f"{n} 件の媒体を初期投入しました。")
            st.rerun()
        return  # 以降の編集 UI は不要

    # ── B'. エイリアス（表記ゆれ）確認 ───────────────────────────────────────
    with st.expander("🔍 表記ゆれエイリアス確認（自動生成 + 手動 + DB保存分）", expanded=False):
        st.caption(
            "AI 抽出時に「これらの語が含まれていれば該当媒体にマッチ」する全エイリアスを表示します。"
            "意図しないマッチが出る場合は、編集テーブルの「エイリアス」列から削除してください。"
        )
        resolve_master = build_resolve_master()
        rows = []
        for _, row in master_df.iterrows():
            sid = row["sid"]
            site_name = row["site_name"]
            mk = make_key(sid, site_name)
            aliases = resolve_master.get(mk, [])
            # 元のサイト名と完全一致するもの・空白だけのものを除外して見やすく
            display_aliases = sorted({a for a in aliases if a and a != site_name.lower()})
            rows.append({
                "SID": sid,
                "サイト名": site_name,
                "区分": row["category"],
                "捕捉される表記": " / ".join(display_aliases) if display_aliases else "（自動生成なし）",
                "件数": len(display_aliases),
            })
        alias_df = pd.DataFrame(rows)
        st.dataframe(alias_df, use_container_width=True, hide_index=True, height=400)

        # ── 動作確認: テキストを入れて resolve をシミュレート ─────────────
        st.markdown("##### ✅ 動作確認（resolve シミュレーション）")
        test_text = st.text_input(
            "テストしたい文字列（業務ログの一部 etc.）",
            placeholder="例: エポスの担当者と打合せ。SID 1805522 も交渉中",
            key="alias_test_input",
        )
        if test_text:
            from normalize import resolve_media_name as _rmn
            result = _rmn(test_text, master=resolve_master, fallback=None)
            if result:
                st.success(f"マッチ: **{result}**")
            else:
                st.info("マッチする媒体は見つかりませんでした。")

    # ── C. 既存マスターを data_editor で編集 ─────────────────────────────
    st.markdown("### ✏️ 既存マスターを編集")
    st.caption("SID とサイト名は主キーのため変更不可。担当者・URL・区分・エイリアスは自由に編集できます。")

    # data_editor 用に aliases を文字列化
    ver = st.session_state.get("master_editor_ver", 0)
    display_df = master_df.copy()
    display_df["aliases_str"] = display_df["aliases"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else ""
    )

    edited_df = st.data_editor(
        display_df[["sid", "site_name", "site_url", "media_contact", "category", "aliases_str"]],
        column_config={
            "sid":          st.column_config.TextColumn("SID",      disabled=True, width="small"),
            "site_name":    st.column_config.TextColumn("サイト名", disabled=True, width="large"),
            "site_url":     st.column_config.TextColumn("URL",      width="medium"),
            "media_contact":st.column_config.TextColumn("担当者",   width="small"),
            "category":     st.column_config.SelectboxColumn("区分", options=CATEGORY_CHOICES, width="small"),
            "aliases_str":  st.column_config.TextColumn("エイリアス（カンマ区切り）", width="medium"),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"master_editor_{ver}",
    )

    if st.button("変更を保存", type="primary", key="btn_save_master"):
        # aliases_str → list に変換
        save_df = edited_df.copy()
        save_df["sid"]       = master_df["sid"].values
        save_df["site_name"] = master_df["site_name"].values
        save_df["aliases"]   = save_df["aliases_str"].apply(
            lambda x: [a.strip() for a in str(x).split(",") if a.strip()]
        )
        save_media_master(save_df)
        st.session_state["master_editor_ver"] = ver + 1
        _flash("success", "媒体マスターを保存しました。")
        st.rerun()

    st.markdown("---")

    # ── D. 新規媒体を追加 ─────────────────────────────────────────────────
    st.markdown("### ➕ 新規媒体を追加")
    with st.form("frm_add_media", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            new_sid     = st.text_input("SID（数字）",  placeholder="例: 9999999")
            new_name    = st.text_input("サイト名",      placeholder="例: 新規ポイントモール")
            new_contact = st.text_input("担当者名",      placeholder="例: 山田 基道")
        with c2:
            new_url      = st.text_input("サイト URL",  placeholder="https://example.com/")
            new_category = st.selectbox("区分", CATEGORY_CHOICES)

        if st.form_submit_button("追加する", type="primary"):
            new_sid  = new_sid.strip()
            new_name = new_name.strip()
            if not new_sid or not new_name:
                _flash("warning", "SID とサイト名は必須です。")
            elif not new_sid.isdigit():
                _flash("warning", "SID は数字のみで入力してください。")
            else:
                key = add_media(new_sid, new_name, new_url, new_contact, new_category)
                if key is None:
                    _flash("error", f"SID {new_sid} はすでに登録されています。")
                else:
                    _flash("success", f"媒体「{key}」を追加しました。")
            st.rerun()

    st.markdown("---")

    # ── E. 媒体を削除（ソフト削除） ────────────────────────────────────────
    st.markdown("### 🗑️ 媒体を削除（非表示化）")
    st.warning(
        "削除した媒体は `is_active=False` に変更されて非表示になります。"
        "全広告主のこの媒体に関するステータス・メモも参照されなくなります。"
    )
    media_keys = get_media_keys()
    if not media_keys:
        st.caption("削除できる媒体がありません。")
        return

    del_target = st.selectbox("削除する媒体", media_keys, key="del_media")
    confirmed  = st.checkbox(
        f"「{del_target}」が非表示になることを理解しました。",
        key="del_confirm",
    )
    if st.button("削除実行", type="primary", disabled=(not confirmed), key="btn_del_media"):
        remove_media_column(del_target)
        delete_media(del_target)
        _flash("success", f"「{del_target}」を非表示にしました。")
        st.rerun()


# ======== メイン ============================================================

def main() -> None:
    _init_state()
    require_password()          # ← 認証ゲート（未認証はここで止まる）

    st.title("📊 CS アフィリエイト媒体管理ダッシュボード")
    render_sidebar()
    _show_flash()

    tab_a, tab_b, tab_c, tab_d = st.tabs([
        "① クライアント個別管理",
        "② AI 抽出（モック）",
        "③ 全体進捗一覧",
        "④ 媒体マスター管理",
    ])
    with tab_a:
        render_client_tab()
    with tab_b:
        render_ai_tab()
    with tab_c:
        render_overview_tab()
    with tab_d:
        render_master_tab()


if __name__ == "__main__":
    main()
