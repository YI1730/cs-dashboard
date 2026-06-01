-- ============================================================
-- マイグレーション 001: ステータスを5値化 + 構造化フィールド5本追加
--
-- 旧: status + note の2フィールド
-- 新: status + monthly_conversions + reward_terms +
--     negotiation_status + ineligible_reason + memo の6フィールド
--
-- 既に schema.sql v1 を適用済みの DB に対して安全に実行できる。
-- Supabase ダッシュボード → SQL Editor → New query で実行。
-- ============================================================

-- ── 1. 新カラムを追加（存在すれば skip） ─────────────────────────────────────
alter table client_media_status
    add column if not exists monthly_conversions text not null default '',
    add column if not exists reward_terms        text not null default '',
    add column if not exists negotiation_status  text not null default '',
    add column if not exists ineligible_reason   text not null default '',
    add column if not exists memo                text not null default '';

-- ── 2. 旧ステータス値を新5値へマッピング ────────────────────────────────────
update client_media_status set status = '未掲載'   where status = '未着手';
update client_media_status set status = '交渉中'   where status = '提案中';
update client_media_status set status = '掲載不可' where status in ('掲載終了', '提案不可');
-- 想定外の値が残っていれば '未掲載' に寄せる
update client_media_status set status = '未掲載'
    where status not in ('未掲載', '交渉中', 'レス待ち', '掲載中', '掲載不可');

-- ── 3. 既存 note の内容を memo にコピー（memo が空のときのみ） ────────────
-- ※「note は AI が触る領域」「memo は触らない領域」と分離するため、
--   旧 note の内容は保護対象である memo へ寄せる安全方針。
update client_media_status
   set memo = note
   where coalesce(memo, '') = ''
     and coalesce(note, '') <> '';

-- ── 4. 旧 note カラムを削除 ────────────────────────────────────────────────
alter table client_media_status drop column if exists note;
