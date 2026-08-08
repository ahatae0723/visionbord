# -*- coding: utf-8 -*-
"""
【投稿案づくりの材料集めスクリプト】
data/ に貯まった過去投稿のインサイトから「何が伸びたか」を整理して表示する。
Claude Code が投稿案を生成するとき（/draft コマンド）の参考資料になる。

このスクリプトは読むだけで、何も書き換えません。投稿もしません。

実行方法:  python3 scripts/draft_context.py
"""
from collections import defaultdict

from weekly_report import (
    POSTS_CSV,
    WEEKDAYS_JP,
    engagement_rate,
    fmt_rate,
    parse_dt,
    read_csv,
    slot_of,
    themes_of,
    to_int,
)

# 投稿データがこの件数未満なら、傾向分析はせず tone.md のみを根拠に生成する
MIN_POSTS_FOR_ANALYSIS = 4


def main():
    posts = [p for p in read_csv(POSTS_CSV) if to_int(p.get("views")) > 0]
    posts.sort(key=lambda p: to_int(p.get("views")), reverse=True)

    print("=" * 60)
    print("投稿案づくりの材料（過去投稿のインサイト分析）")
    print("=" * 60)

    if len(posts) < MIN_POSTS_FOR_ANALYSIS:
        print(f"\n📌 データのある投稿が {len(posts)} 件しかありません（{MIN_POSTS_FOR_ANALYSIS}件未満）。")
        print("→ 傾向分析はまだ行わず、【tone.md のみを根拠】に生成してください。\n")
        if posts:
            print("参考までに、現時点のデータ:")
            for p in posts:
                print(f"  - {p.get('posted_at')} 閲覧{to_int(p.get('views')):,} "
                      f"ER{fmt_rate(engagement_rate(p))}: {p.get('text', '')[:40]}")
        return

    print(f"\nデータのある投稿: {len(posts)}件\n")

    print("■ 閲覧数トップ5（書き出し・テーマの参考に）")
    for p in posts[:5]:
        print(f"  - 閲覧{to_int(p.get('views')):,} ER{fmt_rate(engagement_rate(p))} "
              f"({p.get('posted_at')}): {p.get('text', '')[:60]}")

    er_posts = sorted(
        (p for p in posts if engagement_rate(p) is not None),
        key=engagement_rate, reverse=True,
    )
    print("\n■ エンゲージメント率トップ3（「刺さった」投稿の型の参考に）")
    for p in er_posts[:3]:
        print(f"  - ER{fmt_rate(engagement_rate(p))} 閲覧{to_int(p.get('views')):,}: "
              f"{p.get('text', '')[:60]}")

    slot_v, wd_v, theme_v = defaultdict(list), defaultdict(list), defaultdict(list)
    for p in posts:
        v = to_int(p.get("views"))
        dt = parse_dt(p.get("posted_at"))
        if dt:
            slot_v[slot_of(dt.hour)].append(v)
            wd_v[WEEKDAYS_JP[dt.weekday()]].append(v)
        for t in themes_of(p.get("text")):
            theme_v[t].append(v)

    def show(title, d):
        print(f"\n■ {title}")
        for k, vals in sorted(d.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True):
            print(f"  - {k}: 平均{int(sum(vals) / len(vals)):,}（{len(vals)}件）")

    show("テーマ別の平均閲覧数", theme_v)
    show("時間帯別の平均閲覧数", slot_v)
    show("曜日別の平均閲覧数", wd_v)

    # 投稿の記録が20件以上貯まっていれば、「型」別の傾向も出す
    from tag_analysis import summary_for_drafts
    print("\n■ 「型」別の傾向（投稿の記録から。24時間後の閲覧数の中央値で比較）")
    for line in summary_for_drafts():
        print(f"  - {line}")

    print("\n→ この傾向を材料に、tone.md のルールに従って投稿案を作ってください。")


if __name__ == "__main__":
    main()
