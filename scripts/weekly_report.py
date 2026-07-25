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
HISTORY_CSV = DATA_DIR / "post_history.csv"  # 投稿ごとの数字を毎日記録したもの

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

# 投稿時間帯の区切り。
# 実際の投稿習慣（4時台〜5時台の「夜明け前」に集中）に合わせてある。
# 4:55と5:03が別の枠に分かれると、同じ習慣が2つに割れて分析がおかしくなるため、
# 4〜6時をひとつの「早朝」枠にまとめている。区切りを変えたいときはここを直す。
TIME_SLOTS = [
    ("深夜（0〜3時）", 0, 3),
    ("早朝（4〜6時）", 4, 6),
    ("朝（7〜9時）", 7, 9),
    ("昼（10〜15時）", 10, 15),
    ("夕方（16〜19時）", 16, 19),
    ("夜（20〜23時）", 20, 23),
]

# 投稿直後は閲覧数が積み上がる途中なので、これより若い投稿は「伸びた/伸びなかった」の
# 比較分析から除外する（トップ3の紹介や総閲覧数には、除外せず含める）。
MIN_AGE_HOURS_FOR_ANALYSIS = 24


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
        "days": days,
        "days_recorded": len(days),
        "account_views": total_views,  # アカウント全体の日別views合計（古い投稿が見られた分も含む）
        "post_views": sum(to_int(p.get("views")) for p in week_posts),  # 期間内に投稿された投稿のviews合計（累計）
        "followers_start": followers_start,
        "followers_end": followers_end,
        "posts": week_posts,
    }


def mermaid_chart(title, labels, values, kind="bar", y_label="閲覧数"):
    """
    GitHub上で自動的にグラフとして描画されるMermaid記法（xychart）を作る。
    kind は "bar"（棒グラフ）か "line"（折れ線）。
    """
    lbls = ", ".join('"{}"'.format(str(l).replace('"', "'")) for l in labels)
    vals = ", ".join(str(int(v)) for v in values)
    return "\n".join([
        "```mermaid",
        # グラフの色をはっきりした青に指定（標準色は薄くて見づらいため）
        '%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "#4269d0"}}}}%%',
        "xychart-beta",
        f'    title "{title}"',
        f"    x-axis [{lbls}]",
        f'    y-axis "{y_label}"',
        f"    {kind} [{vals}]",
        "```",
    ])


def short_date(date_str):
    """'2026-07-08' → '7/8' のような短い表記にする（グラフの横軸用）。"""
    return f"{int(date_str[5:7])}/{int(date_str[8:10])}"


def has_value(row, field):
    """その行のその項目に数字が入っているか。
    APIエラーで取れなかった日は空欄になるので、「0」と区別するために使う。"""
    return str(row.get(field, "")).strip() != ""


def trend_chart(title, days, field, y_label, lines):
    """日別の推移グラフを追加する。取得できなかった日はグラフから飛ばす（0で描かない）。"""
    valid = [r for r in days if has_value(r, field)]
    if len(valid) < 2:
        return
    lines.append(mermaid_chart(
        title,
        [short_date(r["date"]) for r in valid],
        [to_int(r[field]) for r in valid],
        "line", y_label,
    ))
    lines.append("")
    missing = [short_date(r["date"]) for r in days if not has_value(r, field)]
    if missing:
        lines.append(f"※ {'、'.join(missing)} は取得に失敗したため、グラフから外しています"
                     "（閲覧が0だったわけではありません）。")
        lines.append("")


def post_age_hours(post, now):
    """投稿されてから何時間経ったか。日時が読めなければ None。"""
    dt = parse_dt(post.get("posted_at"))
    return (now - dt).total_seconds() / 3600 if dt else None


def views_at_24h(post, history_by_post):
    """
    投稿から24時間後に近い時点の閲覧数を返す（履歴がなければ None）。
    投稿の新しさによる有利・不利をなくして比べるために使う。
    """
    posted = parse_dt(post.get("posted_at"))
    snapshots = history_by_post.get(post.get("post_id"), [])
    if not posted or not snapshots:
        return None
    best, best_diff = None, None
    for snap in snapshots:
        taken = parse_dt(snap.get("fetched_at"))
        if not taken:
            continue
        age = (taken - posted).total_seconds() / 3600
        if not 12 <= age <= 48:  # 24時間から離れすぎている記録は使わない
            continue
        diff = abs(age - 24)
        if best_diff is None or diff < best_diff:
            best, best_diff = snap, diff
    return to_int(best.get("views")) if best else None


def load_post_history():
    """data/post_history.csv（投稿ごとの日々の記録）を投稿IDごとにまとめて返す。"""
    history = defaultdict(list)
    for row in read_csv(HISTORY_CSV):
        history[row.get("post_id", "")].append(row)
    return history


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

    # 投稿直後の投稿は閲覧数が積み上がる途中なので、比較分析からは外す。
    # （例: 昨日の投稿と5日前の投稿を並べると、新しいほうが不当に低く見えてしまう）
    now = datetime.now(JST)
    mature_posts = [p for p in week_posts
                    if (post_age_hours(p, now) or 0) >= MIN_AGE_HOURS_FOR_ANALYSIS]
    young_count = len(week_posts) - len(mature_posts)

    # 「伸びなかった投稿」= 閲覧数下位半分（比較対象は出そろった投稿のみ）
    bottom_half = mature_posts[len(mature_posts) // 2:] if len(mature_posts) >= 4 else []

    # 投稿から24時間後時点の閲覧数（履歴が貯まっている投稿だけ）
    history_by_post = load_post_history()
    fair_views = {}
    for p in week_posts:
        v24 = views_at_24h(p, history_by_post)
        if v24 is not None:
            fair_views[p["post_id"]] = v24

    # 時間帯・曜日・テーマ別の平均閲覧数（出そろった投稿だけで計算）
    slot_views, weekday_views, theme_views = defaultdict(list), defaultdict(list), defaultdict(list)
    for p in mature_posts:
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
    pc = len(this_week["posts"])
    ppc = len(prev_week["posts"]) if has_prev else None

    # 総閲覧数 = 期間内に投稿された投稿のviews合計（投稿ごとのviewsは取得時点までの累計）
    tv = this_week["post_views"]
    pv = prev_week["post_views"] if has_prev else None
    lines.append(f"- **総閲覧数（今週の投稿{pc}件の閲覧数合計）**: {fmt_num(tv)} {fmt_delta(tv, pv)}")

    view_days = sum(1 for r in this_week["days"] if has_value(r, "views"))
    if view_days > 0:
        lines.append(
            f"- **アカウント全体の表示回数**: {fmt_num(this_week['account_views'])}"
            f"（取得できた{view_days}日分の合計。過去の投稿が見られた分も含むため、上の数字とは対象が異なります）"
        )

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

    lines.append(f"- **投稿数**: {pc}件 {fmt_delta(pc, ppc)}")
    if this_week["days_recorded"] < 7:
        lines.append(f"- ℹ️ 今週はデータが{this_week['days_recorded']}日ぶんしかありません（貯まるほど正確になります）")
    lines.append("")

    # --- 推移グラフ（2日以上データがあるとき。取得失敗の日は飛ばして描く） ---
    days = this_week["days"]
    if len(days) >= 2:
        lines.append("### 📈 日別の閲覧数の推移（アカウント全体）")
        lines.append("")
        trend_chart("1日ごとにアカウント全体が見られた回数", days, "views", "閲覧数", lines)
        lines.append("### 👥 フォロワー数の推移")
        lines.append("")
        trend_chart("フォロワー数", days, "followers_count", "人", lines)

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
            age = post_age_hours(p, now)
            if age is not None and age < MIN_AGE_HOURS_FOR_ANALYSIS:
                lines.append(f"- ℹ️ 投稿から{int(age)}時間しか経っていないため、数字はまだ伸びる途中です")
            if p.get("permalink"):
                lines.append(f"- [投稿を見る]({p['permalink']})")
            lines.append("")
    else:
        lines.append("今週の投稿データがまだありません。")
        lines.append("")

    # --- 24時間後の閲覧数で揃えた比較（履歴が2件以上あるとき） ---
    if len(fair_views) >= 2:
        lines.append("## ⚖️ 投稿から24時間後の閲覧数（条件を揃えた比較）")
        lines.append("")
        lines.append("閲覧数は投稿後もずっと増え続けるので、古い投稿ほど有利に見えます。"
                     "ここでは「投稿から24時間後」の時点で揃えて比べています。")
        lines.append("")
        ranked = sorted(
            (p for p in week_posts if p["post_id"] in fair_views),
            key=lambda p: fair_views[p["post_id"]], reverse=True,
        )
        lines.append(mermaid_chart(
            "投稿から24時間後の閲覧数",
            # 同じ日に2本投稿することがあるので、時刻まで入れて区別する
            [p.get("posted_at", "?")[5:].replace("-", "/") for p in ranked],
            [fair_views[p["post_id"]] for p in ranked],
        ))
        lines.append("")
        lines.append("| 投稿日時 | 24時間後の閲覧数 | 現在の閲覧数 | 本文の冒頭 |")
        lines.append("|---|---|---|---|")
        for p in ranked:
            lines.append(f"| {p.get('posted_at', '?')} | {fmt_num(fair_views[p['post_id']])} "
                         f"| {fmt_num(to_int(p.get('views')))} | {excerpt(p.get('text'), 20)} |")
        lines.append("")

    # --- 伸びなかった投稿との違い ---
    lines.append("## 🔍 伸びた投稿・伸びなかった投稿の違い")
    lines.append("")
    if young_count:
        lines.append(f"ℹ️ 投稿から24時間経っていない{young_count}件は、数字が出そろっていないため"
                     "この比較からは外しています。")
        lines.append("")
    if len(mature_posts) >= 4:
        top_half = mature_posts[: len(mature_posts) // 2]
        top_avg = sum(to_int(p.get("views")) for p in top_half) / len(top_half)
        low_avg = sum(to_int(p.get("views")) for p in bottom_half) / len(bottom_half)
        lines.append(f"伸びた投稿（上位半分）の平均閲覧数は **{fmt_num(int(top_avg))}**、"
                     f"伸びなかった投稿（下位半分）は **{fmt_num(int(low_avg))}** でした。")
        lines.append("")

        if slot_avg:
            lines.append("**時間帯別の平均閲覧数**")
            lines.append("")
            if len(slot_avg) >= 2:
                slot_names = [n for n, _, _ in TIME_SLOTS if n in slot_avg]
                lines.append(mermaid_chart(
                    "どの時間帯の投稿が伸びたか（平均閲覧数）",
                    [n.split("（")[0] for n in slot_names],
                    [slot_avg[n][0] for n in slot_names],
                ))
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
            if len(weekday_avg) >= 2:
                wd_names = [d for d in WEEKDAYS_JP if d in weekday_avg]
                lines.append(mermaid_chart(
                    "どの曜日の投稿が伸びたか（平均閲覧数）",
                    wd_names,
                    [weekday_avg[d][0] for d in wd_names],
                ))
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
            sorted_theme_items = sorted(theme_avg.items(), key=lambda kv: kv[1][0], reverse=True)
            if len(theme_avg) >= 2:
                lines.append(mermaid_chart(
                    "どのテーマの投稿が伸びたか（平均閲覧数）",
                    [t for t, _ in sorted_theme_items],
                    [v[0] for _, v in sorted_theme_items],
                ))
                lines.append("")
            lines.append("| テーマ | 平均閲覧数 | 投稿数 |")
            lines.append("|---|---|---|")
            for theme, (avg, count) in sorted(theme_avg.items(), key=lambda kv: kv[1][0], reverse=True):
                lines.append(f"| {theme} | {fmt_num(int(avg))} | {count}件 |")
            lines.append("")
    else:
        lines.append("数字が出そろった投稿がまだ少ないため（4件未満）、比較分析は来週以降に行います。")
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
