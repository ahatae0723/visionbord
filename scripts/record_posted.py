# -*- coding: utf-8 -*-
"""
【投稿記録スクリプト】
実際にThreadsへ投稿した投稿案を data/posted.csv に記録する。
後日（データが20件以上貯まったら）、インサイトと突き合わせて
「どんなタグの投稿が伸びたか」を学習するための材料になる。

※ このスクリプトは記録するだけ。分析はしない。投稿もしない。

使い方:
  投稿を記録する:
    python3 scripts/record_posted.py 2026-07-14-1 --permalink https://www.threads.com/...
    （--permalink は省略可。省略した場合、投稿IDは翌朝の自動突き合わせで埋まる）

  投稿IDの自動突き合わせ（毎朝の日次取得後に自動実行される）:
    python3 scripts/record_posted.py --backfill
"""
import argparse
import re
import sys
from datetime import datetime

from fetch_daily import POSTS_CSV, upsert_csv
from weekly_report import read_csv
from threads_api import DATA_DIR, JST, ROOT, log

POSTED_CSV = DATA_DIR / "posted.csv"
POSTED_FIELDS = [
    "draft_id", "post_id", "permalink", "posted_at",
    "theme", "hook_type", "has_case", "ending_type", "char_count", "has_hashtag",
    "recorded_at",
]
TAG_KEYS = ["theme", "hook_type", "has_case", "ending_type", "char_count", "has_hashtag"]


def normalize_link(url: str) -> str:
    """URLの表記ゆれ（?以降のパラメータ、末尾の/、threads.net/.comの違い）をならす。"""
    url = (url or "").split("?")[0].rstrip("/")
    return url.replace("threads.net", "threads.com")


def load_draft_tags(draft_id: str) -> dict:
    """drafts/YYYY-MM-DD.md から、指定した案のタグを読み取る。"""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}(?:_\d+)?)-(\d+)$", draft_id)
    if not m:
        sys.exit(f"エラー: 案IDの形式が違います（例: 2026-07-14-1）: {draft_id}")
    draft_file = ROOT / "drafts" / f"{m.group(1)}.md"
    if not draft_file.exists():
        sys.exit(f"エラー: 下書きファイルが見つかりません: {draft_file}")

    text = draft_file.read_text(encoding="utf-8")
    # draft_id が書かれている案のセクションを探す
    sections = re.split(r"^## ", text, flags=re.M)
    section = next((s for s in sections if f"draft_id: {draft_id}" in s), None)
    if section is None:
        sys.exit(f"エラー: {draft_file.name} に draft_id: {draft_id} のタグが見つかりません。")

    tags = {}
    for key in TAG_KEYS:
        found = re.search(rf"-\s*{key}:\s*(.+)", section)
        tags[key] = found.group(1).strip() if found else ""
    return tags


def find_post(permalink: str) -> dict | None:
    """data/posts.csv から、リンクが一致する投稿を探す。"""
    target = normalize_link(permalink)
    if not target:
        return None
    for p in read_csv(POSTS_CSV):
        if normalize_link(p.get("permalink", "")) == target:
            return p
    return None


def normalize_text(text: str) -> str:
    """本文照合用に、空白・改行を取り除いてならす。"""
    return re.sub(r"\s+", "", text or "")


def load_draft_body(draft_id: str) -> str:
    """drafts/YYYY-MM-DD.md から、指定した案の投稿本文（コードブロック内）を取り出す。"""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}(?:_\d+)?)-(\d+)$", draft_id)
    if not m:
        return ""
    draft_file = ROOT / "drafts" / f"{m.group(1)}.md"
    if not draft_file.exists():
        return ""
    sections = re.split(r"^## ", draft_file.read_text(encoding="utf-8"), flags=re.M)
    section = next((s for s in sections if f"draft_id: {draft_id}" in s), "")
    body = re.search(r"```\n(.*?)```", section, re.S)
    return body.group(1) if body else ""


def find_post_by_text(draft_id: str) -> dict | None:
    """URLが無い記録のために、案の本文の書き出しと一致する投稿を posts.csv から探す。"""
    head = normalize_text(load_draft_body(draft_id))[:25]
    if len(head) < 10:
        return None
    matches = [p for p in read_csv(POSTS_CSV)
               if normalize_text(p.get("text", "")).startswith(head)]
    return matches[0] if len(matches) == 1 else None  # 複数一致は曖昧なので埋めない


def record(draft_id: str, permalink: str, posted_at: str) -> None:
    tags = load_draft_tags(draft_id)
    post = find_post(permalink) if permalink else find_post_by_text(draft_id)
    row = {
        "draft_id": draft_id,
        "post_id": post["post_id"] if post else "",
        "permalink": normalize_link(permalink) if permalink else (post.get("permalink", "") if post else ""),
        "posted_at": posted_at or (post["posted_at"] if post else ""),
        "recorded_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        **tags,
    }
    upsert_csv(POSTED_CSV, POSTED_FIELDS, {draft_id: row}, "draft_id")
    log(f"posted.csv に記録しました: {draft_id}"
        + (f"（投稿ID: {row['post_id']}）" if row["post_id"]
           else "（投稿IDは翌朝の日次取得後に自動で埋まります）"))


def backfill() -> None:
    """post_id が空の記録を、posts.csv と突き合わせて埋める。"""
    rows = read_csv(POSTED_CSV)
    if not rows:
        return
    filled = 0
    updates = {}
    for r in rows:
        if r.get("post_id"):
            continue
        # URLがあればURLで、無ければ本文の書き出しで突き合わせる
        post = find_post(r.get("permalink", "")) or find_post_by_text(r["draft_id"])
        if post:
            if not r.get("permalink"):
                r["permalink"] = post.get("permalink", "")
            r["post_id"] = post["post_id"]
            if not r.get("posted_at"):
                r["posted_at"] = post["posted_at"]
            filled += 1
        updates[r["draft_id"]] = r
    if updates:
        upsert_csv(POSTED_CSV, POSTED_FIELDS, updates, "draft_id")
    if filled:
        log(f"posted.csv の投稿IDを {filled} 件埋めました。")


def main():
    parser = argparse.ArgumentParser(description="投稿した案を posted.csv に記録する")
    parser.add_argument("draft_id", nargs="?", help="案ID（例: 2026-07-14-1）")
    parser.add_argument("--permalink", default="", help="投稿のURL（あれば投稿IDを即時突き合わせ）")
    parser.add_argument("--posted-at", default="", help="投稿日時（例: 2026-07-14 21:00）省略可")
    parser.add_argument("--backfill", action="store_true", help="空の投稿IDを posts.csv と突き合わせて埋める")
    args = parser.parse_args()

    if args.backfill:
        backfill()
    elif args.draft_id:
        record(args.draft_id, args.permalink, args.posted_at)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
