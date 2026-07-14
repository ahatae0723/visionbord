# -*- coding: utf-8 -*-
"""
【日次取得スクリプト】
Threads からその日のインサイト（数字）を取ってきて、data/ フォルダのCSVに貯める。

- data/account_daily.csv … アカウント全体の数字（フォロワー数・その日の閲覧数）を1日1行
- data/posts.csv         … 投稿ごとの最新の数字（閲覧・いいね・リプライなど）を投稿1件1行
- 同じ日に2回実行しても、その日の行を上書きするだけなので重複しません。

実行方法（手動で試すとき）:  python scripts/fetch_daily.py
"""
import csv
import sys
from datetime import datetime, timedelta

from threads_api import (
    JST,
    DATA_DIR,
    ThreadsAPIError,
    api_get,
    get_token,
    init_token_status_if_missing,
    log,
    token_days_left,
)

ACCOUNT_CSV = DATA_DIR / "account_daily.csv"
POSTS_CSV = DATA_DIR / "posts.csv"

ACCOUNT_FIELDS = ["date", "followers_count", "views", "fetched_at"]
POST_FIELDS = [
    "post_id", "posted_at", "text", "permalink", "media_type",
    "views", "likes", "replies", "reposts", "quotes", "shares",
    "last_updated",
]

# 投稿は直近この日数ぶんをまとめて取り直す（後から伸びる「いいね」等も反映されるように）
POST_LOOKBACK_DAYS = 30


def fetch_account_insights(token: str, user_id: str) -> dict:
    """アカウント全体の数字（フォロワー数・今日の閲覧数）を取得する。"""
    result = {"followers_count": "", "views": ""}

    # フォロワー数（現在の合計）
    try:
        data = api_get(f"{user_id}/threads_insights", token,
                       {"metric": "followers_count"})
        for item in data.get("data", []):
            if item.get("name") == "followers_count":
                result["followers_count"] = item.get("total_value", {}).get("value", "")
    except ThreadsAPIError as e:
        if e.is_auth_error:
            raise
        log(f"  フォロワー数の取得に失敗（スキップして続行）: {e}")

    # 今日の閲覧数（日別）。「今日」1日ぶんの views を取る
    try:
        now = datetime.now(JST)
        since = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        until = int(now.timestamp())
        data = api_get(f"{user_id}/threads_insights", token,
                       {"metric": "views", "period": "day",
                        "since": since, "until": until})
        for item in data.get("data", []):
            if item.get("name") == "views":
                values = item.get("values", [])
                if values:
                    result["views"] = values[-1].get("value", "")
    except ThreadsAPIError as e:
        if e.is_auth_error:
            raise
        log(f"  閲覧数の取得に失敗（スキップして続行）: {e}")

    return result


def fetch_recent_posts(token: str) -> list[dict]:
    """直近の投稿一覧（本文・投稿日時）を取得する。ページを順にたどる。"""
    since = int((datetime.now(JST) - timedelta(days=POST_LOOKBACK_DAYS)).timestamp())
    posts = []
    params = {
        "fields": "id,text,timestamp,media_type,permalink,is_quote_post",
        "since": since,
        "limit": 50,
    }
    url = "me/threads"
    while True:
        data = api_get(url, token, params)
        for item in data.get("data", []):
            posts.append(item)
        next_url = data.get("paging", {}).get("next")
        if not next_url or len(posts) >= 200:
            break
        url, params = next_url, {}  # next には必要な情報が全部入っている
    return posts


def fetch_post_insights(token: str, post_id: str) -> dict:
    """1つの投稿の数字（閲覧・いいね・リプライ・リポスト・引用・シェア）を取得する。"""
    metrics = {"views": "", "likes": "", "replies": "", "reposts": "", "quotes": "", "shares": ""}
    try:
        data = api_get(f"{post_id}/insights", token,
                       {"metric": "views,likes,replies,reposts,quotes,shares"})
        for item in data.get("data", []):
            name = item.get("name")
            if name in metrics:
                values = item.get("values", [])
                if values:
                    metrics[name] = values[0].get("value", "")
                elif "total_value" in item:
                    metrics[name] = item["total_value"].get("value", "")
    except ThreadsAPIError as e:
        if e.is_auth_error:
            raise
        # リポストなど、数字が取れない種類の投稿もあるのでスキップして続行
        log(f"  投稿 {post_id} の数字が取れませんでした（スキップ）: {e}")
    return metrics


def upsert_csv(path, fieldnames, rows_by_key, key_field):
    """CSVに追記する。同じキー（日付や投稿ID）の行があれば上書きする＝重複しない。"""
    existing = {}
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                existing[row[key_field]] = row
    existing.update(rows_by_key)
    # 古い順に並べて書き戻す
    rows = sorted(existing.values(), key=lambda r: str(r.get(key_field, "")))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean_text(text: str) -> str:
    """CSVが崩れないように、本文の改行を「␣」に置き換える。"""
    return (text or "").replace("\r", " ").replace("\n", " ").strip()


def main():
    log("===== 日次取得を開始します =====")
    token = get_token()
    today = datetime.now(JST).strftime("%Y-%m-%d")
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

    try:
        # 自分のアカウント情報
        me = api_get("me", token, {"fields": "id,username"})
        user_id = me["id"]
        log(f"アカウント確認OK: @{me.get('username', '?')}")

        init_token_status_if_missing()

        # 1) アカウント全体の数字
        account = fetch_account_insights(token, user_id)
        upsert_csv(
            ACCOUNT_CSV, ACCOUNT_FIELDS,
            {today: {"date": today, "fetched_at": now_str, **account}},
            "date",
        )
        log(f"アカウント全体: フォロワー {account['followers_count']} 人 / 今日の閲覧数 {account['views']}")

        # 2) 投稿ごとの数字
        posts = fetch_recent_posts(token)
        log(f"直近{POST_LOOKBACK_DAYS}日間の投稿: {len(posts)} 件")
        rows = {}
        for p in posts:
            insights = fetch_post_insights(token, p["id"])
            # 投稿日時を日本時間の読みやすい形に直す
            ts = p.get("timestamp", "")
            try:
                posted_at = (
                    datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
                    .astimezone(JST)
                    .strftime("%Y-%m-%d %H:%M")
                )
            except ValueError:
                posted_at = ts
            rows[p["id"]] = {
                "post_id": p["id"],
                "posted_at": posted_at,
                "text": clean_text(p.get("text", "")),
                "permalink": p.get("permalink", ""),
                "media_type": p.get("media_type", ""),
                "last_updated": now_str,
                **insights,
            }
        if rows:
            upsert_csv(POSTS_CSV, POST_FIELDS, rows, "post_id")

        days_left = token_days_left()
        if days_left is not None and days_left <= 10:
            log(f"⚠️ 注意: アクセストークンの残りが約{days_left}日です。docs/token_guide.md を見て更新してください。")

        log("===== 日次取得が完了しました =====")

    except ThreadsAPIError as e:
        log(f"エラー: {e}")
        if e.is_auth_error:
            log("→ アクセストークンの期限切れの可能性が高いです。docs/token_guide.md の「トークンが切れたとき」を見てください。")
            sys.exit(2)  # 終了コード2 = トークンエラー（GitHub Actions がお知らせIssueを作る目印）
        sys.exit(1)


if __name__ == "__main__":
    main()
