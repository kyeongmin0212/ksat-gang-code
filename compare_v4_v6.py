"""v4 vs v6 백테스트 + 오늘 후보 비교 리포트."""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
sys.stdout.reconfigure(encoding="utf-8")

import json
from pathlib import Path

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT  = BASE / "logs" / "v4_vs_v6_compare.txt"


def load(name: str) -> dict:
    p = BASE / f"backtest_results_{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def fmt(d: dict, label: str) -> dict:
    if not d:
        return {"name": label, "missing": True}
    ov = d["statistics"]["overall"]
    cr = d["statistics"].get("by_close_reason", {})
    ec = cr.get("event_counts", {})
    bp = d["statistics"].get("by_position_type", {}).get("중장기", {})
    return {
        "name": label,
        "missing": False,
        "trades":     ov.get("total_trades", 0),
        "win_rate":   ov.get("win_rate_pct", 0),
        "avg_return": ov.get("avg_return_pct", 0),
        "avg_win":    ov.get("avg_win_pct", 0),
        "avg_loss":   ov.get("avg_loss_pct", 0),
        "cum_ret":    ov.get("cumulative_return_pct", 0),
        "cagr":       ov.get("cagr_pct", 0),
        "mdd":        ov.get("mdd_pct", 0),
        "sharpe":     ov.get("sharpe", 0),
        "final_pv":   ov.get("final_portfolio_value", 0),
        "avg_hold":   bp.get("avg_hold_days", 0),
        "익절":       ec.get("익절", 0),
        "1차손절":    ec.get("1차손절", 0),
        "2차손절":    ec.get("2차손절", 0),
        "기간초과":   ec.get("기간초과", 0),
        "sl1_pct":    cr.get("sl1_trigger_pct", 0),
        "sl2_pct":    cr.get("sl2_trigger_pct", 0),
    }


def main() -> int:
    v4 = fmt(load("v4"),  "v4")
    v6 = fmt(load("v6"),  "v6")

    out: list[str] = []
    w = out.append

    w("=" * 80)
    w("  v4 vs v6 비교 — 단테 추가 기법 (MA224 + MA112 + 60일 박스권)")
    w("  기간: 2021-04-23 ~ 2026-04-22 (5년) / 초기 자본 1,000만원")
    w("=" * 80)
    w("")

    if v4.get("missing") or v6.get("missing"):
        w("[ERR] 결과 파일 누락:")
        if v4.get("missing"): w("  · backtest_results_v4.json")
        if v6.get("missing"): w("  · backtest_results_v6.json")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out))
        return 1

    w("[변형 정의]")
    w("  v4: 점수4 + base_line_near + not_overheated + 중장기 + 우선주 제외")
    w("       + KOSPI bear 필터 + 30일 쿨다운 + 블랙리스트")
    w("       + 1차 -1.5% 절반매도 + 2차 구름하단 전량 + 분할 1:2:4:3")
    w("  v6: v4 위에 추가:")
    w("       + 종가 > MA224  (단테 부모 라인)")
    w("       + 종가 > MA112  (중기 추세)")
    w("       + 60일 박스권 (변동폭 ≤ 30%)")
    w("")

    # 핵심 성과
    w("─" * 80)
    w("[핵심 성과]")
    w("─" * 80)
    w(f"  {'변형':<5} {'거래':>6} {'승률':>7} {'평균수익':>9} {'CAGR':>8} {'누적':>9} "
      f"{'MDD':>8} {'Sharpe':>7} {'최종PV':>14}")
    for r in [v4, v6]:
        w(f"  {r['name']:<5} {r['trades']:>6,} {r['win_rate']:>6.2f}% "
          f"{r['avg_return']:>+8.2f}% {r['cagr']:>+7.2f}% {r['cum_ret']:>+8.2f}% "
          f"{r['mdd']:>+7.2f}% {r['sharpe']:>7.2f} {r['final_pv']:>14,.0f}")
    w("")

    # 차이
    w("─" * 80)
    w("[v4 → v6 차이 (Δ)]")
    w("─" * 80)
    delta_pairs = [
        ("거래수",       v6["trades"]      - v4["trades"]),
        ("승률 %p",      v6["win_rate"]    - v4["win_rate"]),
        ("CAGR %p",      v6["cagr"]        - v4["cagr"]),
        ("누적 %p",      v6["cum_ret"]     - v4["cum_ret"]),
        ("MDD %p",       v6["mdd"]         - v4["mdd"]),
        ("Sharpe",       v6["sharpe"]      - v4["sharpe"]),
        ("평균보유일",   v6["avg_hold"]    - v4["avg_hold"]),
        ("1차손절 %p",   v6["sl1_pct"]     - v4["sl1_pct"]),
        ("2차손절 %p",   v6["sl2_pct"]     - v4["sl2_pct"]),
    ]
    for label, dv in delta_pairs:
        sign = "+" if dv >= 0 else ""
        w(f"  {label:<14} {sign}{dv:.2f}")
    w("")

    # 매도 사유
    w("─" * 80)
    w("[매도 사유별 이벤트 카운트]")
    w("─" * 80)
    w(f"  {'변형':<5} {'익절':>7} {'1차손절':>9} {'2차손절':>9} "
      f"{'기간초과':>10} {'1차%':>7} {'2차%':>7}")
    for r in [v4, v6]:
        w(f"  {r['name']:<5} {r['익절']:>7,} {r['1차손절']:>9,} "
          f"{r['2차손절']:>9,} {r['기간초과']:>10,} "
          f"{r['sl1_pct']:>6.1f}% {r['sl2_pct']:>6.1f}%")
    w("")

    # 종합 평가
    w("─" * 80)
    w("[종합 평가]")
    w("─" * 80)
    if v6["cagr"] > v4["cagr"] + 1 and v6["mdd"] >= v4["mdd"]:
        verdict = "✅ v6 우수 — 시스템 업그레이드 권장"
    elif v6["cagr"] < v4["cagr"] - 1:
        verdict = "❌ v4 우월 — v4 그대로 유지"
    elif abs(v6["cagr"] - v4["cagr"]) <= 1:
        # MDD/Sharpe 로 안전성 비교
        if v6["sharpe"] > v4["sharpe"] and v6["mdd"] > v4["mdd"]:
            verdict = "△ 비슷한 수익 + v6가 더 안전 → v6 권장"
        elif v6["sharpe"] < v4["sharpe"]:
            verdict = "△ 비슷한 수익 + v4가 안정적 → v4 유지"
        else:
            verdict = "△ 비슷 — 어느 쪽이든 OK, v4 유지가 단순"
    else:
        verdict = "△ v6가 약간 우월하지만 MDD 악화 — 신중 검토"
    w(f"  {verdict}")
    w("")

    # 오늘 후보 비교 (있으면)
    cv4 = BASE / "candidates_v4.json"
    cv6 = BASE / "candidates_v6.json"
    if cv4.exists() and cv6.exists():
        d4 = json.loads(cv4.read_text(encoding="utf-8"))
        d6 = json.loads(cv6.read_text(encoding="utf-8"))
        n4 = len(d4.get("tradable_candidates", []))
        n6 = len(d6.get("tradable_candidates", []))
        rej = d6.get("v6_rejected_candidates", [])

        w("─" * 80)
        w(f"[오늘 후보 비교 (기준일 {d4.get('date', '?')})]")
        w("─" * 80)
        w(f"  v4 후보: {n4}건")
        w(f"  v6 후보: {n6}건  (탈락 {len(rej)}건)")
        w("")
        if rej:
            w("[v4 통과 → v6 탈락 종목]")
            w(f"  {'종목':<14} {'코드':<8} {'탈락 사유':<60}")
            for r in rej:
                reasons = " / ".join(r.get("v6_reject_reasons", []))[:55]
                w(f"  {r.get('name', ''):<14} {r.get('ticker', ''):<8} {reasons}")
            w("")
        v6_pass_names = [c.get("name") for c in d6.get("tradable_candidates", [])]
        if v6_pass_names:
            w(f"[v6 통과 종목 ({len(v6_pass_names)}건)]")
            w("  " + ", ".join(v6_pass_names))
            w("")

    w("=" * 80)

    txt = "\n".join(out)
    print(txt)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(txt, encoding="utf-8")
    print(f"\n[saved] {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
