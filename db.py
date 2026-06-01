"""Supabase クライアント（プロセス間で使い回すシングルトン）。"""
from __future__ import annotations

import streamlit as st
from supabase import Client, create_client


@st.cache_resource(show_spinner=False)
def get_client() -> Client:
    """st.secrets から URL/Key を読み、Supabase クライアントを返す。

    必要な secrets:
        SUPABASE_URL — Project URL
        SUPABASE_KEY — anon public key
    """
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except KeyError as e:
        st.error(
            f"🚨 Supabase の接続情報が未設定です: `{e.args[0]}` が secrets にありません。\n\n"
            "`.streamlit/secrets.toml`（ローカル）または Streamlit Cloud の Settings → Secrets "
            "に設定してください。"
        )
        st.stop()
    return create_client(url, key)
