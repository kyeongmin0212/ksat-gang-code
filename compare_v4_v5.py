"""v4 vs v5 비교 리포트 — backtest_results_v4/v5_A/v5_B/v5_C.json 4개 파싱 후 요약."""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import json
import sys
from pathlib import Path

BASE = Path(r"C:\Users\sji48\ksat_gang")
LOG  = BASE / "logs"

LABELS = [
    ("v4",   "baseline — 1차 -1.5%(기준선), 1:2:4:3, 절반매도"),
    ("v5_A", "1차 -10%, 1:2:4:3, 절반매도"),
    ("v5_B", "1차 -10%, 1:2:4:8 (단테 정통), 절반매도"),
    ("v5_C", "1차 -5%, 1:2:4:3, 절반매도"),
]


def load(name: str) -> dict:
    path = BASE / f"backtest_results_{name}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def fmt_pct(n) -> str:
    try:
        return f"{float(n):+.2f}%"
    except Exception:
        return str(n)


def main() -> int:
    rows = []
    for name, desc in LABELS:
        d = load(name)
        if not d:
            print(f"[WARN] {name} 결과 없음")
            continue
        ov = d["statistics"]["overall"]
        cr = d["statistics"].get("by_close_reason", {})
        ec = cr.get("event_counts", {})
        rows.append({
            "name": name,
            "desc": desc,
            "trades":             ov.get("total_trades", 0),
            "win_rate":           ov.get("win_rate_pct", 0),
            "avg_return":         ov.get("avg_return_pct", 0),
            "avg_win":            ov.get("avg_win_pct", 0),
            "avg_loss":           ov.get("avg_loss_pct", 0),
            "cum_return":         ov.get("cumulative_return_pct", 0),
            "cagr":               ov.get("cagr_pct", 0),
            "mdd":                ov.get("mdd_pct", 0),
            "sharpe":             ov.get("sharpe", 0),
            "final_pv":           ov.get("final_portfolio_value", 0),
            "avg_hold":           d["statistics"].get("by_position_type", {}).get("중장기", {}).get("avg_hold_days", 0),
            "익절":               ec.get("익절", 0),
            "1차손절":             ec.get("1차손절", 0),
            "2차손절":             ec.get("2차손절", 0),
            "기간초과":            ec.get("기간초과", 0),
            "트레일링":            ec.get("트레일링", 0),
            "단일손절":            ec.get("단일손절", 0),
            "sl1_pct":            cr.get("sl1_trigger_pct", 0),
            "sl2_pct":            cr.get("sl2_trigger_pct", 0),
        })

    if not rows:
        print("[ERR] 결과 없음")
        return 1

    out = []
    w = out.append
    w("=" * 78)
    w("  v4 baseline vs v5_A/B/C — 단테 정통 손절 방식 비교")
    w(f"  기간: 2021-04-23 ~ 2026-04-22 (5년)")
    w("=" * 78)
    w("")
    w("[변형 정의]")
    for r in rows:
        w(f"  {r['name']:6s}  {r['desc']}")
    w("")
    w("─" * 78)
    w("[핵심 성과 지표]")
    w("─" * 78)
    w(f"  {'변형':<6} {'거래수':>7} {'승률':>7} {'평균수익':>9} {'CAGR':>8} {'누적':>9} {'MDD':>8} {'Sharpe':>7} {'최종PV':>14}")
    for r in rows:
        w(f"  {r['name']:<6} "
          f"{fmt_int(r['trades']):>7} "
          f"{r['win_rate']:>6.2f}% "
          f"{r['avg_return']:>+8.2f}% "
          f"{r['cagr']:>+7.2f}% "
          f"{r['cum_return']:>+8.2f}% "
          f"{r['mdd']:>+7.2f}% "
          f"{r['sharpe']:>7.2f} "
          f"{fmt_int(r['final_pv']):>14}")
    w("")
    w("─" * 78)
    w("[승/패 분포]")
    w("─" * 78)
    w(f"  {'변형':<6} {'평균승':>9} {'평균패':>9} {'평균보유일':>12}")
    for r in rows:
        w(f"  {r['name']:<6} "
          f"{r['avg_win']:>+8.2f}% "
          f"{r['avg_loss']:>+8.2f}% "
          f"{r['avg_hold']:>11.1f}d")
    w("")
    w("─" * 78)
    w("[매도 사유별 이벤트 카운트]")
    w("─" * 78)
    w(f"  {'변형':<6} {'익절':>7} {'1차손절':>9} {'2차손절':>9} {'기간초과':>10} "
      f"{'1차%':>7} {'2차%':>7}")
    for r in rows:
        w(f"  {r['name']:<6} "
          f"{fmt_int(r['익절']):>7} "
          f"{fmt_int(r['1차손절']):>9} "
          f"{fmt_int(r['2차손절']):>9} "
          f"{fmt_int(r['기간초과']):>10} "
          f"{r['sl1_pct']:>6.1f}% "
          f"{r['sl2_pct']:>6.1f}%")
    w("")
    w("  · 1차%/2차% = 트리거된 포지션 수 / 전체 포지션 수")
    w("  · 익절·기간초과는 sell_event 단위(이벤트 단위) 카운트")
    w("")

    # 순위 매기기
    w("─" * 78)
    w("[순위표 — 핵심 지표별]")
    w("─" * 78)
    metrics = [
        ("CAGR (높을수록 좋음)",   "cagr",        True),
        ("누적 수익률 (높을수록)", "cum_return",  True),
        ("Sharpe (높을수록)",      "sharpe",      True),
        ("MDD (덜 음수일수록)",    "mdd",         True),    # MDD는 음수, 0에 가까울수록 좋음 → True
        ("승률 (높을수록)",        "win_rate",    True),
        ("1차 손절 빈도 (낮을수록)", "sl1_pct",   False),
    ]
    for title, key, higher_better in metrics:
        ranked = sorted(rows, key=lambda r: r[key], reverse=higher_better)
        line = f"  {title:<30} → " + " > ".join(
            f"{r['name']}({r[key]:+.2f})" for r in ranked
        )
        w(line)
    w("")

    # 최종 평가
    w("─" * 78)
    w("[종합 평가]")
    w("─" * 78)
    best_cagr = max(rows, key=lambda r: r["cagr"])
    best_cum  = max(rows, key=lambda r: r["cum_return"])
    best_sharpe = max(rows, key=lambda r: r["sharpe"])
    least_mdd = max(rows, key=lambda r: r["mdd"])
    w(f"  · 최고 CAGR        : {best_cagr['name']} ({best_cagr['cagr']:+.2f}%)")
    w(f"  · 최고 누적 수익률 : {best_cum['name']} ({best_cum['cum_return']:+.2f}%)")
    w(f"  · 최고 Sharpe       : {best_sharpe['name']} ({best_sharpe['sharpe']:.2f})")
    w(f"  · 최소 MDD          : {least_mdd['name']} ({least_mdd['mdd']:+.2f}%)")
    w("")

    # v4 대비 v5 변형
    base = next(r for r in rows if r["name"] == "v4")
    w(f"[v4 baseline 대비 v5 변형 차이]")
    for r in rows:
        if r["name"] == "v4":
            continue
        d_cagr = r["cagr"] - base["cagr"]
        d_cum  = r["cum_return"] - base["cum_return"]
        d_mdd  = r["mdd"] - base["mdd"]
        d_sharpe = r["sharpe"] - base["sharpe"]
        d_sl1  = r["sl1_pct"] - base["sl1_pct"]
        w(f"  {r['name']}: ΔCAGR {d_cagr:+.2f}%p / Δ누적 {d_cum:+.2f}%p / "
          f"ΔMDD {d_mdd:+.2f}%p / ΔSharpe {d_sharpe:+.2f} / Δ1차손절 {d_sl1:+.2f}%p")
    w("")
    w("=" * 78)

    txt = "\n".join(out)
    print(txt)
    out_path = LOG / "v4_vs_v5_compare.txt"
    out_path.write_text(txt, encoding="utf-8")
    print(f"\n[saved] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
