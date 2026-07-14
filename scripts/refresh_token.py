# -*- coding: utf-8 -*-
"""
【トークン自動更新スクリプト】
Threads の長期アクセストークンは約60日で切れるため、毎週このスクリプトで
「新しい60日」に延長（リフレッシュ）する。

GitHub Actions で動かす場合:
  - GH_PAT（GitHubの操作用トークン）が Secrets に設定されていれば、
    更新後の新しいトークンを GitHub Secrets（THREADS_ACCESS_TOKEN）に自動で書き戻す。
    → これで完全自動。トークン切れの心配がなくなる。
  - GH_PAT が無い場合は延長だけ行い、書き戻せない旨をログに残す。
    （60日以内に一度も書き戻せないと期限切れになるので、docs/token_guide.md を参照）

手元のMacで動かす場合:
  python scripts/refresh_token.py
  → 新しいトークンを .env に自動で書き戻す。
"""
import base64
import os
import sys
from datetime import datetime, timedelta

import requests

from threads_api import (
    JST,
    REFRESH_URL,
    ROOT,
    get_token,
    log,
    save_token_status,
)


def refresh_threads_token(token: str) -> tuple[str, int]:
    """Threads に「有効期限を延長して」とお願いし、新しいトークンを受け取る。"""
    resp = requests.get(
        REFRESH_URL,
        params={"grant_type": "th_refresh_token", "access_token": token},
        timeout=30,
    )
    body = resp.json()
    if resp.status_code != 200 or "access_token" not in body:
        raise RuntimeError(f"トークン更新に失敗しました: {body.get('error', body)}")
    return body["access_token"], int(body.get("expires_in", 60 * 24 * 3600))


def update_github_secret(new_token: str) -> bool:
    """新しいトークンを GitHub Secrets に書き戻す（GH_PAT がある場合のみ）。"""
    pat = os.environ.get("GH_PAT", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()  # 例: ahatae0723/threads-insights
    if not pat or not repo:
        return False

    from nacl import encoding, public  # GitHubの金庫に入れるための暗号化ライブラリ

    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
    }
    # 1) 暗号化用の公開鍵をもらう
    key_resp = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=headers, timeout=30,
    )
    key_resp.raise_for_status()
    key_data = key_resp.json()

    # 2) トークンを暗号化する
    public_key = public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder())
    encrypted = public.SealedBox(public_key).encrypt(new_token.encode())

    # 3) Secrets に保存する
    put_resp = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/THREADS_ACCESS_TOKEN",
        headers=headers,
        json={
            "encrypted_value": base64.b64encode(encrypted).decode(),
            "key_id": key_data["key_id"],
        },
        timeout=30,
    )
    put_resp.raise_for_status()
    return True


def update_env_file(new_token: str) -> bool:
    """手元のMacで実行した場合、.env ファイルの中身を新しいトークンに書き換える。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return False
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith("THREADS_ACCESS_TOKEN="):
            lines[i] = f"THREADS_ACCESS_TOKEN={new_token}"
            updated = True
    if updated:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return updated


def main():
    log("===== トークンの更新を開始します =====")
    token = get_token()

    try:
        new_token, expires_in = refresh_threads_token(token)
    except Exception as e:
        log(f"エラー: {e}")
        log("→ トークンがすでに失効している可能性があります。docs/token_guide.md を見て取り直してください。")
        sys.exit(2)

    days = expires_in // 86400
    expires_at = (datetime.now(JST) + timedelta(seconds=expires_in)).strftime("%Y-%m-%d")
    log(f"トークンを更新しました（あと約{days}日、{expires_at} まで有効）")

    # 新しいトークンを保存先に書き戻す
    saved = False
    if os.environ.get("GITHUB_ACTIONS") == "true":
        saved = update_github_secret(new_token)
        if saved:
            log("新しいトークンを GitHub Secrets に自動保存しました。設定作業は不要です。")
        else:
            log("⚠️ GH_PAT が未設定のため、GitHub Secrets への自動保存はできませんでした。")
            log("   Secrets の中身は古いトークンのまま（元の期限で切れます）。")
            log("   docs/setup_github.md の「GH_PAT の設定」を行うと完全自動になります。")
    else:
        saved = update_env_file(new_token)
        if saved:
            log("新しいトークンを .env ファイルに保存しました。")
        else:
            log("⚠️ .env ファイルが見つからなかったため、新しいトークンを表示します。")
            log("   下の1行を .env の THREADS_ACCESS_TOKEN= の後ろに貼り替えてください:")
            print(new_token)

    # 有効期限メモを更新（週次レポートの「残り日数」表示に使われる）
    # 保存に成功したときだけ延長後の期限を記録する（失敗時は古いトークンのままなので記録しない）
    if saved:
        save_token_status({
            "last_refreshed": datetime.now(JST).strftime("%Y-%m-%d"),
            "expires_at": expires_at,
            "note": "refresh_token.py により自動更新",
        })

    log("===== トークンの更新が完了しました =====")


if __name__ == "__main__":
    main()
