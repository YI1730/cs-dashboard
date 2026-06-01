"""簡易パスワード認証ゲート。"""
from __future__ import annotations

import streamlit as st


def require_password() -> None:
    """認証されていなければログインフォームを表示し、以降の処理を停止する。"""
    if st.session_state.get("_authed"):
        return

    expected = st.secrets.get("PASSWORD")
    if not expected:
        st.error("🚨 サーバー設定エラー: `PASSWORD` シークレットが未設定です。")
        st.code('PASSWORD = "your-strong-password"', language="toml")
        st.stop()

    st.markdown("## 🔒 ログイン")
    with st.form("login_form", clear_on_submit=False):
        password = st.text_input(
            "パスワード", type="password", placeholder="管理者パスワードを入力"
        )
        submitted = st.form_submit_button("ログイン", type="primary")

    if submitted:
        if password == expected:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")

    st.caption("このアプリは関係者のみが利用できます。")
    st.stop()


def logout_button() -> None:
    """サイドバーなどに設置するログアウトボタン。"""
    if st.button("ログアウト", key="logout_btn"):
        st.session_state["_authed"] = False
        st.rerun()
