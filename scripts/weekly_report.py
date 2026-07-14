# -*- coding: utf-8 -*-
"""
【週次レポート生成スクリプト】
data/ フォルダに貯まった数字から、直近7日間の日本語レポート（Markdown）を
reports/ フォルダに作る。毎週月曜の朝に自動実行される想定。

実行方法（手動で試すとき）:  python scripts/weekly_report.py
特定の週を作りたいとき:      python scripts/weekly_report.py 2026-07-13
（↑ その日を「レポート作成日」とみなし、その前日までの7日間を集計）
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from threads_api import DATA_DIR, JST, REPORTS_DIR, log, token_days_left

ACCOUNT_CSV = DATA_DIR / "account_daily.csv"
POSTS_CSV = DATA_DIR / "posts.csv"

WEEKDAYS_JP = ["月", "火", "水", "木", "金", "土", "日"]

# 発信テーマ（発達グレーゾーンの子の癇癪・子育て）に合わせたキーワード分類。
# 自由に追加・変更してOKです。投稿本文にこれらの言葉が含まれるかで「テーマ」を判定します。
THEME_KEYWORDS = {
    "癇癪・感情": ["癇癪", "かんしゃく", "パニック", "泣き", "怒り", "イライラ", "爆発", "感情"],
    "発達特性": ["発達", "グレーゾーン", "ADHD", "ASD", "自閉", "特性", "凸凹", "感覚過敏"],
    "学校・園": ["学校", "登校", "先生", "園", "幼稚園", "保育園", "支援級", "通級", "行き渋り"],
    "声かけ・対応法": ["声かけ", "対応", "接し方", "伝え方", "ほめ", "褒め", "叱", "切り替え"],
    "親のケア": ["ママ", "お母さん", "親", "自分を責め", "疲れ", "しんどい", "休", "罪悪感"],
    "生活習慣": ["朝", "夜", "宿題", "ゲーム", "YouTube", "ごはん", "食事", "寝", "支度", "偏食"],
}

# 投稿時間帯の区切り
TIME_SLOTS = [
    ("早朝（5〜8時）", 5, 8),
    ("午前（9〜12時）", 9, 12),
    ("午後（13〜16時）", 13, 16),
    ("夕方（17〜20時）", 17, 20),
    ("夜（21〜24時）", 21, 23),
    ("深夜（0〜4時）", 0, 4),
]


def read_csv(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_int(value, default=0):
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def parse_dt(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except (ValueError, TypeError):
        return None


def engagement_rate(post):
    """エンゲージメント率 = （いいね＋リプライ＋リポスト）÷ 閲覧数"""
    views = to_int(post.get("views"))
    if views <= 0:
        return None
    engaged = to_int(post.get("likes")) + to_int(post.get("replies")) + to_int(post.get("reposts"))
    return engaged / views


def fmt_rate(rate):
    return f"{rate * 100:.1f}%" if rate is not None else "—"


def fmt_num(n):
    return f"{n:,}"


def fmt_delta(current, previous):
    """前週比を「+123 (+12%)」のような文字列にする。"""
    if previous is None:
        return "（前週データなし）"
    diff = current - previous
    sign = "+" if diff >= 0 else ""
    if previous != 0:
        pct = diff / previous * 100
        return f"{sign}{fmt_num(diff)}（前週比 {sign}{pct:.0f}%）"
    return f"{sign}{fmt_num(diff)}（前週は0）"


def excerpt(text, length=40):
    text = (text or "").strip()
    return text[:length] + ("…" if len(text) > length else "")


def slot_of(hour):
    for name, start, end in TIME_SLOTS:
        if start <= hour <= end:
            return name
    return "不明"


def themes_of(text):
    found = [name for name, words in THEME_KEYWORDS.items()
             if any(w.lower() in (text or "").lower() for w in words)]
    return found or ["その他"]


def week_stats(account_rows, posts, start, end):
    """指定期間（start以上end未満）の集計を返す。"""
    days = [r for r in account_rows
            if start.strftime("%Y-%m-%d") <= r["date"] < end.strftime("%Y-%m-%d")]
    total_views = sum(to_int(r["views"]) for r in days)
    followers_start = to_int(days[0]["followers_count"]) if days else None
    followers_end = to_int(days[-1]["followers_count"]) if days else None

    week_posts = []
    for p in posts:
        dt = parse_dt(p.get("posted_at"))
        if dt and start <= dt < end:
            week_posts.append(p)
    return {
        "days_recorded": len(days),
        "total_views": total_views,
        "followers_start": followers_start,
        "followers_end": followers_end,
        "posts": week_posts,
    }


def build_suggestions(this_week, slot_avg, weekday_avg, theme_avg, top_posts, low_posts):
    """データから改善提案を組み立てる（当てはまるものから最大3つ）。"""
    suggestions = []

    if slot_avg:
        best_slot = max(slot_avg, key=lambda k: slot_avg[k][0])
        avg, count = slot_avg[best_slot]
        if count >= 2:
            suggestions.append(
                f"**投稿時間を「{best_slot}」に寄せる** — この時間帯の投稿は平均 {fmt_num(int(avg))} 閲覧と最も伸びています"
                f"（{count}件の平均）。悩みを検索するママは子どもの就寝後や登校後に時間ができることが多いので、この傾向と合っているか来週も確認しましょう。"
            )

    if weekday_avg:
        best_day = max(weekday_avg, key=lambda k: weekday_avg[k][0])
        avg, count = weekday_avg[best_day]
        if count >= 2:
            suggestions.append(
                f"**{best_day}曜日に力を入れた投稿を置く** — {best_day}曜日の平均閲覧数が {fmt_num(int(avg))} と高めです。"
                f"一番伝えたいノウハウ系の投稿はこの曜日に合わせると届きやすくなります。"
            )

    if theme_avg:
        sorted_themes = sorted(theme_avg.items(), key=lambda kv: kv[1][0], reverse=True)
        best_theme, (avg, count) = sorted_themes[0]
        if count >= 2 and best_theme != "その他":
            suggestions.append(
                f"**「{best_theme}」テーマを週2〜3本に増やす** — このテーマの平均閲覧数（{fmt_num(int(avg))}）が最も高く、"
                f"フォロワーさんの関心が集まっています。同じテーマでも「具体的な場面＋そのときの声かけ例」まで書くと保存されやすくなります。"
            )

    if top_posts:
        rates = [engagement_rate(p) for p in top_posts]
        rates = [r for r in rates if r is not None]
        if rates and max(rates) >= 0.05:
            suggestions.append(
                "**反応率が高かった投稿の「書き出し」を再利用する** — トップ投稿は最初の1行で悩みの場面を言い当てています。"
                "同じ型（場面の描写→共感→具体策）で別の場面に置き換えた投稿を作ってみましょう。"
            )

    if low_posts and top_posts:
        low_avg_len = sum(len(p.get("text", "")) for p in low_posts) / len(low_posts)
        top_avg_len = sum(len(p.get("text", "")) for p in top_posts) / len(top_posts)
        if top_avg_len > low_avg_len * 1.5:
            suggestions.append(
                "**短すぎる投稿を減らす** — 伸びた投稿は伸びなかった投稿より本文が長め（具体的）でした。"
                "「一言つぶやき」よりも、場面が目に浮かぶ具体例つきの投稿を優先しましょう。"
            )

    post_count = len(this_week["posts"])
    if post_count < 5:
        suggestions.append(
            f"**投稿数を増やす** — 今週の投稿は{post_count}件でした。まずは1日1投稿（週7件）を目安にすると、"
            "どのテーマ・時間帯が伸びるかのデータも早く貯まります。"
        )

    if not suggestions:
        suggestions.append("データがまだ少ないため、来週も同じペースで投稿を続けてデータを貯めましょう。")
    return suggestions[:3]


def main():
    log("===== 週次レポート生成を開始します =====")

    # レポート作成日（引数で指定できる。通常は今日＝月曜朝）
    if len(sys.argv) > 1:
        base = datetime.strptime(sys.argv[1], "%Y-%m-%d").replace(tzinfo=JST)
    else:
        base = datetime.now(JST)
    base = base.replace(hour=0, minute=0, second=0, microsecond=0)

    this_start = base - timedelta(days=7)   # 直近7日間（前日まで）
    this_end = base
    prev_start = base - timedelta(days=14)  # その前の7日間（前週比用）
    prev_end = this_start

    account_rows = read_csv(ACCOUNT_CSV)
    posts = read_csv(POSTS_CSV)

    if not account_rows and not posts:
        log("データがまだありません。先に fetch_daily.py を実行してデータを貯めてください。")
        sys.exit(0)

    this_week = week_stats(account_rows, posts, this_start, this_end)
    prev_week = week_stats(account_rows, posts, prev_start, prev_end)
    has_prev = prev_week["days_recorded"] > 0 or len(prev_week["posts"]) > 0

    week_posts = sorted(this_week["posts"], key=lambda p: to_int(p.get("views")), reverse=True)
    top3 = week_posts[:3]
    # 「伸びなかった投稿」= 閲覧数下位半分（トップ3と重ならないように下から取る）
    bottom_half = week_posts[len(week_posts) // 2:] if len(week_posts) >= 4 else []

    # 時間帯・曜日・テーマ別の平均閲覧数
    slot_views, weekday_views, theme_views = defaultdict(list), defaultdict(list), defaultdict(list)
    for p in week_posts:
        dt = parse_dt(p.get("posted_at"))
        views = to_int(p.get("views"))
        if dt:
            slot_views[slot_of(dt.hour)].append(views)
            weekday_views[WEEKDAYS_JP[dt.weekday()]].append(views)
        for theme in themes_of(p.get("text")):
            theme_views[theme].append(views)

    def averages(d):
        return {k: (sum(v) / len(v), len(v)) for k, v in d.items() if v}

    slot_avg, weekday_avg, theme_avg = averages(slot_views), averages(weekday_views), averages(theme_views)

    # ---------- Markdown を組み立てる ----------
    lines = []
    period_label = f"{this_start.strftime('%Y年%m月%d日')} 〜 {(this_end - timedelta(days=1)).strftime('%m月%d日')}"
    lines.append(f"# Threads 週次レポート（{period_label}）")
    lines.append("")
    lines.append(f"アカウント: @ai_hattatu.schoolsocialworker ／ 作成日時: {datetime.now(JST).strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # --- 今週のサマリー ---
    lines.append("## 📊 今週のサマリー")
    lines.append("")
    tv = this_week["total_views"]
    pv = prev_week["total_views"] if has_prev else None
    lines.append(f"- **総閲覧数**: {fmt_num(tv)} {fmt_delta(tv, pv)}")

    if this_week["followers_end"] is not None:
        f_end = this_week["followers_end"]
        f_start = this_week["followers_start"]
        f_diff = f_end - f_start if f_start is not None else 0
        sign = "+" if f_diff >= 0 else ""
        prev_diff = None
        if has_prev and prev_week["followers_end"] is not None and prev_week["followers_start"] is not None:
            prev_diff = prev_week["followers_end"] - prev_week["followers_start"]
        prev_label = f"（前週の増減: {'+' if prev_diff >= 0 else ''}{prev_diff}人）" if prev_diff is not None else ""
        lines.append(f"- **フォロワー数**: {fmt_num(f_end)}人（今週 {sign}{f_diff}人）{prev_label}")
    else:
        lines.append("- **フォロワー数**: データなし")

    pc = len(this_week["posts"])
    ppc = len(prev_week["posts"]) if has_prev else None
    lines.append(f"- **投稿数**: {pc}件 {fmt_delta(pc, ppc)}")
    if this_week["days_recorded"] < 7:
        lines.append(f"- ℹ️ 今週はデータが{this_week['days_recorded']}日ぶんしかありません（貯まるほど正確になります）")
    lines.append("")

    # --- トップ3投稿 ---
    lines.append("## 🏆 閲覧数トップ3の投稿")
    lines.append("")
    if top3:
        for i, p in enumerate(top3, 1):
            rate = engagement_rate(p)
            lines.append(f"### {i}位: 「{excerpt(p.get('text'))}」")
            lines.append("")
            lines.append(f"- 投稿日時: {p.get('posted_at', '?')}")
            lines.append(
                f"- 閲覧 {fmt_num(to_int(p.get('views')))} ／ いいね {fmt_num(to_int(p.get('likes')))}"
                f" ／ リプライ {fmt_num(to_int(p.get('replies')))} ／ リポスト {fmt_num(to_int(p.get('reposts')))}"
            )
            lines.append(f"- **エンゲージメント率: {fmt_rate(rate)}**（いいね＋リプライ＋リポスト ÷ 閲覧数）")
            if p.get("permalink"):
                lines.append(f"- [投稿を見る]({p['permalink']})")
            lines.append("")
    else:
        lines.append("今週の投稿データがまだありません。")
        lines.append("")

    # --- 伸びなかった投稿との違い ---
    lines.append("## 🔍 伸びた投稿・伸びなかった投稿の違い")
    lines.append("")
    if len(week_posts) >= 4:
        top_half = week_posts[: len(week_posts) // 2]
        top_avg = sum(to_int(p.get("views")) for p in top_half) / len(top_half)
        low_avg = sum(to_int(p.get("views")) for p in bottom_half) / len(bottom_half)
        lines.append(f"伸びた投稿（上位半分）の平均閲覧数は **{fmt_num(int(top_avg))}**、"
                     f"伸びなかった投稿（下位半分）は **{fmt_num(int(low_avg))}** でした。")
        lines.append("")

        if slot_avg:
            lines.append("**時間帯別の平均閲覧数**")
            lines.append("")
            lines.append("| 時間帯 | 平均閲覧数 | 投稿数 |")
            lines.append("|---|---|---|")
            for name, _, _ in TIME_SLOTS:
                if name in slot_avg:
                    avg, count = slot_avg[name]
                    lines.append(f"| {name} | {fmt_num(int(avg))} | {count}件 |")
            lines.append("")

        if weekday_avg:
            lines.append("**曜日別の平均閲覧数**")
            lines.append("")
            lines.append("| 曜日 | 平均閲覧数 | 投稿数 |")
            lines.append("|---|---|---|")
            for day in WEEKDAYS_JP:
                if day in weekday_avg:
                    avg, count = weekday_avg[day]
                    lines.append(f"| {day} | {fmt_num(int(avg))} | {count}件 |")
            lines.append("")

        if theme_avg:
            lines.append("**テーマ別の平均閲覧数**（本文のキーワードから自動分類）")
            lines.append("")
            lines.append("| テーマ | 平均閲覧数 | 投稿数 |")
            lines.append("|---|---|---|")
            for theme, (avg, count) in sorted(theme_avg.items(), key=lambda kv: kv[1][0], reverse=True):
                lines.append(f"| {theme} | {fmt_num(int(avg))} | {count}件 |")
            lines.append("")
    else:
        lines.append("投稿数がまだ少ないため（4件未満）、比較分析は来週以降に行います。")
        lines.append("")

    # --- 改善提案 ---
    lines.append("## 💡 来週に向けた改善提案")
    lines.append("")
    for i, s in enumerate(
        build_suggestions(this_week, slot_avg, weekday_avg, theme_avg, top3, bottom_half), 1
    ):
        lines.append(f"{i}. {s}")
    lines.append("")

    # --- トークンの残り日数 ---
    lines.append("---")
    days_left = token_days_left()
    if days_left is not None:
        if days_left <= 10:
            lines.append(f"⚠️ **アクセストークンの残りは約{days_left}日です。** "
                         "docs/token_guide.md の「トークンが切れたとき」を見て、早めに更新してください。")
        else:
            lines.append(f"🔑 アクセストークンの残り: 約{days_left}日（60日ごとに更新が必要です）")
    lines.append("")

    # ---------- ファイルに保存 ----------
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = REPORTS_DIR / f"weekly_{this_start.strftime('%Y-%m-%d')}_{(this_end - timedelta(days=1)).strftime('%Y-%m-%d')}.md"
    filename.write_text("\n".join(lines), encoding="utf-8")
    log(f"レポートを作成しました: {filename}")
    log("===== 週次レポート生成が完了しました =====")


if __name__ == "__main__":
    main()
