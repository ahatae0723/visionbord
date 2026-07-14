# -*- coding: utf-8 -*-
"""
Threads API との通信をまとめた共通モジュール。

「アクセストークン」= Threadsのデータを取り出すための合鍵のような文字列。
このファイルを直接実行することはありません（他のスクリプトから使われます）。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# 日本時間（JST = 世界標準時 + 9時間）
JST = timezone(timedelta(hours=9))

BASE_URL = "https://graph.threads.net/v1.0"
REFRESH_URL = "https://graph.threads.net/refresh_access_token"

# このプロジェクトのルートフォルダ（scripts/ の1つ上）
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
LOG_DIR = ROOT / "logs"
TOKEN_STATUS_FILE = DATA_DIR / "token_status.json"


def log(message: str) -> None:
    """時刻つきでメッセージを画面とログファイルに残す。"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {message}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"{datetime.now(JST).strftime('%Y-%m')}.log"
    with open(logfile, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_token() -> str:
    """アクセストークンを環境変数（または .env ファイル）から読み込む。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        log("エラー: アクセストークンが見つかりません。")
        log("  → .env ファイル、または GitHub Secrets に THREADS_ACCESS_TOKEN を設定してください。")
        log("  → 設定方法は docs/token_guide.md を見てください。")
        sys.exit(1)
    return token


class ThreadsAPIError(Exception):
    """Threads API がエラーを返したときに使う例外。"""

    def __init__(self, message, is_auth_error=False):
        super().__init__(message)
        self.is_auth_error = is_auth_error


def api_get(path: str, token: str, params: dict | None = None, retries: int = 3) -> dict:
    """
    Threads API に問い合わせて結果（JSON）を返す。
    一時的な通信エラーは自動で数回やり直す。
    """
    url = path if path.startswith("http") else f"{BASE_URL}/{path.lstrip('/')}"
    params = dict(params or {})
    params["access_token"] = token

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            body = resp.json() if resp.content else {}
            if resp.status_code == 200:
                return body
            err = body.get("error", {})
            code = err.get("code")
            msg = err.get("message", resp.text[:300])
            # code 190 = トークン切れ・無効。やり直しても無駄なのですぐ知らせる
            if code == 190 or resp.status_code in (401, 403):
                raise ThreadsAPIError(
                    f"アクセストークンが無効です（期限切れの可能性）: {msg}",
                    is_auth_error=True,
                )
            last_error = f"HTTP {resp.status_code}: {msg}"
        except ThreadsAPIError:
            raise
        except requests.RequestException as e:
            last_error = f"通信エラー: {e}"
        if attempt < retries:
            wait = 2 ** attempt
            log(f"  再試行します（{attempt}/{retries - 1}回目、{wait}秒待機）... 原因: {last_error}")
            time.sleep(wait)
    raise ThreadsAPIError(f"APIエラー（{retries}回試しても失敗）: {last_error}")


def load_token_status() -> dict:
    """トークンの有効期限メモ（data/token_status.json）を読む。無ければ空。"""
    if TOKEN_STATUS_FILE.exists():
        try:
            return json.loads(TOKEN_STATUS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_token_status(status: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_STATUS_FILE.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def init_token_status_if_missing() -> None:
    """
    有効期限メモが無い場合、「今日から60日有効」と仮定して作る。
    （長期トークンの有効期限は約60日のため。実際より短めに見積もっても害はない）
    """
    status = load_token_status()
    if not status.get("expires_at"):
        now = datetime.now(JST)
        status = {
            "last_refreshed": now.strftime("%Y-%m-%d"),
            "expires_at": (now + timedelta(days=60)).strftime("%Y-%m-%d"),
            "note": "初回実行時に自動作成（60日有効と仮定）。トークンを更新したら refresh_token.py が自動で書き換えます。",
        }
        save_token_status(status)
        log(f"トークンの有効期限メモを作成しました（{status['expires_at']} まで有効と仮定）")


def token_days_left() -> int | None:
    """トークンの残り日数を返す。メモが無ければ None。"""
    status = load_token_status()
    expires_at = status.get("expires_at")
    if not expires_at:
        return None
    try:
        expiry = datetime.strptime(expires_at, "%Y-%m-%d").replace(tzinfo=JST)
    except ValueError:
        return None
    return (expiry - datetime.now(JST)).days
