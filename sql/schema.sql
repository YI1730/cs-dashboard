-- ============================================================
-- CS アフィリエイト媒体管理ダッシュボード — Supabase スキーマ（v2）
-- 新規プロジェクト用：Supabase ダッシュボード → SQL Editor → New query
-- 既存環境からのアップグレードは sql/migrate_001_status_fields.sql を実行
-- ============================================================

-- ── 1. 広告主マスター ────────────────────────────────────────────────────────
create table if not exists advertisers (
    name       text primary key,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- ── 2. 媒体マスター ──────────────────────────────────────────────────────────
create table if not exists media_master (
    sid           text primary key,
    site_name     text not null,
    site_url      text not null default '',
    media_contact text not null default '',
    category      text not null default '',
    aliases       jsonb not null default '[]'::jsonb,
    is_active     boolean not null default true,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists idx_media_master_active   on media_master(sid) where is_active = true;
create index if not exists idx_media_master_aliases  on media_master using gin(aliases);

-- ── 3. クライアント × 媒体 ステータス＆詳細（縦持ち） ─────────────────────
-- 5 つの構造化フィールドと 1 つの保護メモを保持。
-- 各フィールドは status により意味が異なる:
--   掲載中:  monthly_conversions + reward_terms
--   交渉中:  negotiation_status
--   レス待ち: negotiation_status（最後にどの段階か）
--   掲載不可: ineligible_reason
-- memo は AI 更新時に絶対に書き換えないユーザー保護領域。
create table if not exists client_media_status (
    id                  bigserial primary key,
    advertiser_name     text not null
        references advertisers(name) on update cascade on delete cascade,
    media_sid           text not null
        references media_master(sid) on update cascade on delete cascade,
    status              text not null default '未掲載',
    monthly_conversions text not null default '',     -- 掲載中: 月間獲得件数
    reward_terms        text not null default '',     -- 掲載中: 経済条件 (報酬率)
    negotiation_status  text not null default '',     -- 交渉中 / レス待ち: 交渉ステータス
    ineligible_reason   text not null default '',     -- 掲載不可: 掲載不可理由
    memo                text not null default '',     -- ★ ユーザー保護メモ（AI が触らない）
    updated_at          timestamptz not null default now(),
    unique (advertiser_name, media_sid)
);

create index if not exists idx_cms_adv on client_media_status(advertiser_name);
create index if not exists idx_cms_sid on client_media_status(media_sid);

-- ── 4. 戦略履歴（追記型 INSERT only） ────────────────────────────────────────
create table if not exists client_strategy (
    id                    bigserial primary key,
    date                  text not null,
    advertiser_name       text not null
        references advertisers(name) on update cascade on delete cascade,
    topic_summary         text not null default '',
    focus_theme           text not null default '',
    pending_issue         text not null default '',
    base_commission       text not null default '',
    negotiable_commission text not null default ''
);

create index if not exists idx_strategy_adv_date
    on client_strategy(advertiser_name, date desc);

-- ── 5. updated_at 自動更新トリガー ────────────────────────────────────────────
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end; $$;

create or replace trigger trg_adv_updated
    before update on advertisers
    for each row execute function set_updated_at();

create or replace trigger trg_media_updated
    before update on media_master
    for each row execute function set_updated_at();

create or replace trigger trg_cms_updated
    before update on client_media_status
    for each row execute function set_updated_at();
