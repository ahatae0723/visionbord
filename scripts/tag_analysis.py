# -*- coding: utf-8 -*-
"""
【「型」別の傾向を集計するモジュール】
どんな投稿が伸びやすいかを、記録しておいたタグ（テーマ・書き出しの型・締めの型など）
ごとに集計する。週次レポートと投稿案づくりの両方から使われる。

比較には「投稿から24時間後の閲覧数」を使う。
閲覧数は投稿後もずっと増えるので、そのまま比べると古い投稿ほど有利になってしまうため。

このファイルを直接実行すると、いまの集計結果を画面に表示する（確認用）:
  python3 scripts/tag_analysis.py
"""
import statistics
from collections import defaultdict

from record_posted import POSTED_CSV
from weekly_report import (
    POSTS_CSV,
    load_post_history,
    read_csv,
    to_int,
    views_at_24h,
)

# 記録がこの件数に届くまでは、傾向として読むには不確かなので分析を出さない
MIN_RECORDS = 20
# 1つのグループがこの件数未満なら、その比較は出さない（たまたまの結果になるため）
MIN_PER_GROUP = 3

# 中央値の差がこの割合より小さければ「差はない」と見なす（誤読を防ぐため）
NEGLIGIBLE_DIFF = 0.15

# 下書きのタグから比べる項目。(タグ名, 見出し, 説明)
COMPARISONS = [
    ("theme", "テーマ", "癇癪対応／グレーゾーンの関わり方／その他（あるあるなど）"),
    ("hook_type", "書き出しの型", "1行目の入り方"),
    ("ending_type", "締めの型", "最後の1行の形"),
    ("has_case", "架空事例", "「たとえば、こんな場面」のような事例を使ったか"),
]


def build_rows():
    """
    posted.csv（タグ）と posts.csv（数字）と post_history.csv（履歴）を突き合わせて、
    分析用の行を作る。
    """
    posts = {p["post_id"]: p for p in read_csv(POSTS_CSV)}
    history = load_post_history()

    rows = []
    for r in read_csv(POSTED_CSV):
        post = posts.get(r.get("post_id", ""))
        if not post:
            continue  # まだ投稿IDが紐づいていない記録は飛ばす
        rows.append({
            **r,
            "views": to_int(post.get("views")),
            "v24": views_at_24h(post, history),
            "engaged": (to_int(post.get("likes")) + to_int(post.get("replies"))
                        + to_int(post.get("reposts"))),
            # ハッシュタグは本文から直接判定できるので、タグより本文を信用する
            # （下書きを書き換えて投稿した場合でも正しくなる）
            "has_hashtag": "あり" if "#" in post.get("text", "") else "なし",
        })
    return rows


def build_all_post_rows():
    """
    posts.csv の全投稿から分析用の行を作る（下書きを使わずに投稿したものも含む）。
    本文だけで判定できる項目（ハッシュタグの有無など）は、こちらで比べたほうが
    件数が多くなり、傾向がはっきりする。
    """
    history = load_post_history()
    rows = []
    for post in read_csv(POSTS_CSV):
        rows.append({
            "post_id": post.get("post_id", ""),
            "posted_at": post.get("posted_at", ""),
            "views": to_int(post.get("views")),
            "v24": views_at_24h(post, history),
            "engaged": (to_int(post.get("likes")) + to_int(post.get("replies"))
                        + to_int(post.get("reposts"))),
            "has_hashtag": "あり" if "#" in post.get("text", "") else "なし",
        })
    return rows


def diff_note(result):
    """
    上位と下位の中央値の差が小さいときは「差はない」と伝える一言を返す。
    数字の上下だけを見て誤解しないようにするための注意書き。
    """
    if len(result) < 2:
        return ""
    medians = [r[2] for r in result]
    top, bottom = max(medians), min(medians)
    if top <= 0:
        return ""
    if (top - bottom) / top < NEGLIGIBLE_DIFF:
        return "→ どれも中央値がほぼ同じで、**意味のある差は出ていません**（どの型でもOK）。"
    return ""


def compare(rows, key, metric="v24"):
    """
    タグごとに平均・中央値・件数を集計して、**中央値**の高い順に返す。
    （1本の大当たりに引っ張られる平均ではなく、ふだんの実力で並べる）
    件数が MIN_PER_GROUP 未満のグループは除く。
    """
    groups = defaultdict(list)
    for r in rows:
        value = r.get(metric)
        if r.get(key) and value is not None:
            groups[r[key]].append(value)
    result = [
        (name, statistics.mean(v), statistics.median(v), len(v))
        for name, v in groups.items() if len(v) >= MIN_PER_GROUP
    ]
    return sorted(result, key=lambda x: -x[2])


def analysis_lines(rows=None):
    """
    週次レポートに差し込むMarkdownの行リストを返す。
    データが足りないときは、その旨を1行返す。
    """
    rows = build_rows() if rows is None else rows
    lines = ["## 🧾 「型」別の傾向（これまでの記録すべてから）", ""]

    if len(rows) < MIN_RECORDS:
        lines += [f"投稿の記録が{len(rows)}件です。{MIN_RECORDS}件貯まると、"
                  "どんな型の投稿が伸びやすいかの集計を出します。", ""]
        return lines

    with_v24 = [r for r in rows if r.get("v24") is not None]
    lines += [
        f"記録{len(rows)}件のうち、24時間後の数字がそろった{len(with_v24)}件で比較しています。",
        "**平均だけでなく中央値（ちょうど真ん中の値）も載せています。**"
        "1本だけ大きく伸びた投稿があると平均は釣り上がるので、"
        "ふだんの実力は中央値のほうがよく表します。", "",
    ]

    def add_table(label, desc, result):
        lines.extend([f"**{label}別**（{desc}）", "",
                      f"| {label} | 平均 | 中央値 | 件数 |", "|---|---|---|---|"])
        for name, mean, median, n in result:
            lines.append(f"| {name} | {mean:.0f} | **{median:.0f}** | {n}件 |")
        lines.append("")
        note = diff_note(result)
        if note:
            lines.extend([note, ""])

    shown = 0

    # ハッシュタグは本文から判定できるので、下書き外の投稿も含めた全投稿で比べる
    all_rows = [r for r in build_all_post_rows() if r.get("v24") is not None]
    hashtag_result = compare(all_rows, "has_hashtag")
    if len(hashtag_result) >= 2:
        shown += 1
        add_table("ハッシュタグ", "本文にハッシュタグを付けたかどうか。"
                  "下書きを使わずに投稿したものも含めた全投稿で比較", hashtag_result)

    for key, label, desc in COMPARISONS:
        result = compare(with_v24, key)
        if len(result) < 2:  # 比べる相手がいなければ出さない
            continue
        shown += 1
        add_table(label, desc, result)

    if not shown:
        lines += [f"どの項目も、比較できるグループが{MIN_PER_GROUP}件に届いていません。"
                  "投稿の記録が増えると出てきます。", ""]
        return lines

    # 反応（いいね等）の比較も、差が出ている項目だけ載せる
    engaged = compare(rows, "theme", "engaged")
    if len(engaged) >= 2:
        lines += ["**反応（いいね＋リプライ＋リポストの合計）のテーマ別平均**", ""]
        lines.append("｜".join(f"{name} {mean:.1f}（{n}件）" for name, mean, _, n in engaged))
        lines.append("")

    lines += ["ℹ️ 件数が少ない項目は表示していません。"
              "毎週データが増えるので、この表は少しずつ確かになっていきます。", ""]
    return lines


def summary_for_drafts():
    """投稿案づくり（/draft）向けに、要点を短い文字列のリストで返す。"""
    rows = build_rows()
    if len(rows) < MIN_RECORDS:
        return [f"投稿の記録は{len(rows)}件（{MIN_RECORDS}件未満）。型の傾向はまだ使えません。"]
    with_v24 = [r for r in rows if r.get("v24") is not None]
    all_v24 = [r for r in build_all_post_rows() if r.get("v24") is not None]
    out = []
    for key, label, data in ([("has_hashtag", "ハッシュタグ", all_v24)]
                             + [(k, l, with_v24) for k, l, _ in COMPARISONS]):
        result = compare(data, key)
        if len(result) < 2:
            continue
        if diff_note(result):
            out.append(f"{label}: どの型でも差は出ていない（好きな型を使ってよい）")
            continue
        best, worst = result[0], result[-1]
        out.append(
            f"{label}: 「{best[0]}」が有利（中央値{best[2]:.0f}／{best[3]}件） "
            f"↔ 「{worst[0]}」は不利（中央値{worst[2]:.0f}／{worst[3]}件）"
        )
    return out or ["比較できるグループが足りません。"]


if __name__ == "__main__":
    rows = build_rows()
    print(f"分析対象: {len(rows)}件\n")
    print("\n".join(analysis_lines(rows)))
    print("--- 投稿案づくり用の要点 ---")
    for line in summary_for_drafts():
        print(" -", line)
