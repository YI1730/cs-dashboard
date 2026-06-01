# セットアップ & デプロイ ガイド

## 全体の流れ

```
Supabase プロジェクト作成
  ↓
SQL スキーマ流し込み（4テーブル）
  ↓
secrets.toml 設定
  ↓
pip install → ローカル動作確認
  ↓
GitHub にプッシュ（private リポジトリ）
  ↓
Streamlit Community Cloud でデプロイ
  ↓
アプリ内「④ 媒体マスター管理」から初期データ投入
```

---

## STEP 1: Supabase プロジェクトを作成

1. [https://supabase.com](https://supabase.com) にサインアップ（GitHub ログイン推奨）
2. **「New project」** をクリック
3. 設定:
   - Project name: 任意（例: `cs-media-dashboard`）
   - Database Password: 強力なパスワードを設定・保管
   - Region: **Northeast Asia (Tokyo)**
4. **「Create new project」** → 約 2 分待つ

---

## STEP 2: テーブルを作成（DDL 流し込み）

1. Supabase ダッシュボード → 左メニュー「**SQL Editor**」
2. 「**New query**」をクリック
3. `sql/schema.sql` の内容を**全コピー**して貼り付け
4. **「Run」**（▶ ボタン）を押す
5. 「Success. No rows returned」と表示されれば OK

> ⚠️ 以前に古いバージョン（media_status / media_notes の別テーブル構成）を使っていた場合は、先に `drop table if exists media_status, media_notes cascade;` を実行してください。

---

## STEP 3: API キーを取得

1. 左メニュー「**Settings**」→「**API**」
2. 以下をコピー:
   - **Project URL**（例: `https://abcxyz.supabase.co`）
   - **anon public key**（`eyJhbGci...` から始まる長い文字列）

---

## STEP 4: ローカル環境で動作確認

### secrets.toml を作成

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

`.streamlit/secrets.toml` を編集:
```toml
SUPABASE_URL = "https://xxxxxxxxxxxxxxxx.supabase.co"
SUPABASE_KEY = "eyJhbGci..."
PASSWORD     = "強いパスワード（16文字以上推奨）"
```

### 依存ライブラリのインストール

```bash
pip install -r requirements.txt
```

### アプリ起動

```bash
streamlit run app.py
```

ブラウザで `http://localhost:8501` が開く。  
パスワード入力 → 「④ 媒体マスター管理」タブ → **「初期媒体データをシードする」**ボタンを押す（67媒体が一括投入される）。

---

## STEP 5: GitHub に push（private リポジトリ）

```bash
git init
git add .
git commit -m "initial commit"
```

GitHub で **private リポジトリ**を作成し、以下を実行:
```bash
git remote add origin https://github.com/<your-name>/<repo-name>.git
git push -u origin main
```

> `.gitignore` により `.streamlit/secrets.toml` は絶対に push されません。

---

## STEP 6: Streamlit Community Cloud にデプロイ

1. [https://share.streamlit.io](https://share.streamlit.io) にログイン（GitHub アカウント）
2. 「**Create app**」→「Deploy a public app from GitHub」
3. 設定:
   - **Repository**: 先ほど作成した private リポジトリ
   - **Branch**: `main`
   - **Main file path**: `app.py`
4. 「**Advanced settings**」を開き **Secrets** に以下を貼り付け:

```toml
SUPABASE_URL = "https://xxxxxxxxxxxxxxxx.supabase.co"
SUPABASE_KEY = "eyJhbGci..."
PASSWORD     = "強いパスワード"
```

5. 「**Deploy!**」を押す → 約 2 分でデプロイ完了

---

## STEP 7: アクセス制限（自分専用）

### パスワード認証（アプリ組み込み済み）
パスワードゲートが最初に表示され、`secrets.toml` の `PASSWORD` と一致しないと進めません。

### Streamlit Cloud の Viewer 制限（推奨）
1. Streamlit Cloud → アプリの「**Settings**」
2. 「**Sharing**」→「**Only specific people**」
3. 自分の Google アカウントのメールアドレスを追加

これで **ダブルロック** になります。

---

## 注意事項

| 項目 | 詳細 |
|---|---|
| **Supabase 無料枠の一時停止** | 7日間アクセスがないとプロジェクトが自動停止。Supabase ダッシュボードから「Restore」で復帰可能。 |
| **キャッシュ TTL** | データは最大 60 秒キャッシュされる。即時反映したい場合はサイドバーの「🔄 データ再読込」を押す。 |
| **SUPABASE_KEY の漏洩** | anon key が漏れた場合は Supabase → Settings → API → Regenerate で再発行し secrets を更新。 |
| **media_master の is_active** | 削除は `is_active=false` のソフト削除。物理削除したい場合は SQL Editor から直接 DELETE。 |

---

## CSV アップロードによる媒体マスター初期投入（代替手段）

アプリ内ボタン（シード機能）の代わりに、手持ちの CSV をアップロードすることもできます。

**受け付けるカラム名**（日本語・英語どちらも自動マッピング）:

| 項目 | 対応する列名 |
|---|---|
| SID | `SID` / `sid` |
| サイト名 | `サイト名` / `site_name` |
| URL | `URL` / `サイトURL` / `site_url` |
| 担当者 | `担当者` / `メディア担当` / `media_contact` |
| 区分 | `区分` / `AFCST_区分` / `category` |

タブ区切り（`.tsv`）も自動認識します。
