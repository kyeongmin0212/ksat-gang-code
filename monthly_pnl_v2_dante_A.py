"""v2_dante_A 월별 P&L 상세 분석.

- v2_dante_A 재시뮬 → trades + pv_history 확보
- 월별 거래/승률/P&L + 월말 PV + 누적 수익률
- 연속 수익/손실 개월 + TOP 5 best/worst
- ASCII 바 차트

저장: logs/monthly_pnl_v2_dante_A.txt
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt  # noqa

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT_TXT = BASE / "logs" / "monthly_pnl_v2_dante_A.txt"


def configure() -> None:
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False
    bt.CONFIG["exclude_preferred_stocks"] = True
    bt.CONFIG["allowed_position_types"] = ["중장기"]
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False
    bt.CONFIG["enable_trailing_stop"] = False
    bt.CONFIG["target_strategy"] = "median"
    bt.CONFIG["simple_stop_loss_pct"] = None


def month_end_pv(pv_history: list[tuple[str, float]]) -> dict[str, float]:
    """각 월의 마지막 거래일 PV 반환 {YYYYMM: pv}."""
    by_month: dict[str, tuple[str, float]] = {}
    for ds, pv in pv_history:
        m = ds[:6]
        prev = by_month.get(m)
        if prev is None or ds > prev[0]:
            by_month[m] = (ds, pv)
    return {m: pv for m, (_, pv) in sorted(by_month.items())}


def month_iter(start_ym: str, end_ym: str):
    """YYYYMM 월 순회."""
    y, m = int(start_ym[:4]), int(start_ym[4:6])
    ey, em = int(end_ym[:4]), int(end_ym[4:6])
    while (y, m) <= (ey, em):
        yield f"{y:04d}{m:02d}"
        m += 1
        if m > 12:
            m = 1
            y += 1


def bar(pnl: float, max_abs: float, width: int = 30) -> str:
    """ASCII 수익 막대 — pnl 기준 + 방향."""
    if max_abs <= 0:
        return " " * width
    ratio = min(1.0, abs(pnl) / max_abs)
    n = int(round(ratio * width))
    if pnl > 0:
        return " " * width + "│" + "█" * n + " " * (width - n)
    elif pnl < 0:
        return " " * (width - n) + "▓" * n + "│" + " " * width
    else:
        return " " * width + "│" + " " * width


def main() -> int:
    configure()
    bt.log.info("=" * 70)
    bt.log.info("v2_dante_A 월별 P&L 분석 재시뮬")

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)
    kospi_regime = bt.load_kospi_regime(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    trades, pv_history, _sim_meta = bt.simulate(df, bt.CONFIG, kospi_regime)
    del df

    bt.log.info(f"시뮬 완료 / {time.time()-t0:.1f}s / 거래 {len(trades):,}건 — 월별 분석 시작")

    initial_capital = float(bt.CONFIG["initial_capital"])

    # 월별 거래 집계
    by_ym: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        ds = t.close_date or t.open_date
        ym = ds[:6]
        d = by_ym[ym]
        d["trades"] += 1
        if t.realized_pnl() > 0:
            d["wins"] += 1
        d["pnl"] += t.realized_pnl()

    # 월말 PV
    pv_by_month = month_end_pv(pv_history)

    # 전체 월 순회 (2021-04 ~ 2026-04)
    months = list(month_iter("202104", "202604"))

    # 월별 누적 row 구축
    rows = []
    prev_pv = initial_capital
    for m in months:
        tr = by_ym.get(m, {"trades": 0, "wins": 0, "pnl": 0.0})
        end_pv = pv_by_month.get(m, prev_pv)  # 없으면 전월 값 유지
        pv_change = end_pv - prev_pv
        cum_ret = (end_pv - initial_capital) / initial_capital * 100
        win_rate = (tr["wins"] / tr["trades"] * 100) if tr["trades"] else None
        rows.append({
            "ym": m,
            "trades": tr["trades"],
            "wins": tr["wins"],
            "win_rate": win_rate,
            "pnl": tr["pnl"],        # 월 중 실현 P&L (거래 기준)
            "end_pv": end_pv,        # 월말 PV (M2M 포함)
            "pv_change": pv_change,  # 전월 대비 PV 증감
            "cum_ret_pct": cum_ret,
        })
        prev_pv = end_pv

    # 연속 수익/손실 스트릭 (PV 변화 기준)
    max_up, max_down = 0, 0
    cur_up = cur_down = 0
    up_span = down_span = ("", "", 0)
    for r in rows:
        if r["trades"] == 0:
            # 매매 없는 달은 streak 계속 유지 (리셋 아님)
            continue
        if r["pv_change"] > 0:
            cur_up += 1
            cur_down = 0
            if cur_up > max_up:
                max_up = cur_up
                # 시작은 cur_up 개월 전
                # 간단화: span의 끝 ym만 기록
                up_span = (rows[rows.index(r) - cur_up + 1]["ym"], r["ym"], cur_up)
        elif r["pv_change"] < 0:
            cur_down += 1
            cur_up = 0
            if cur_down > max_down:
                max_down = cur_down
                down_span = (rows[rows.index(r) - cur_down + 1]["ym"], r["ym"], cur_down)
        else:
            cur_up = cur_down = 0

    # 평균 월 수익
    monthly_pct_changes = []
    prev = initial_capital
    for r in rows:
        if prev > 0:
            monthly_pct_changes.append((r["end_pv"] - prev) / prev * 100)
        prev = r["end_pv"]
    avg_monthly_pct = float(np.mean(monthly_pct_changes)) if monthly_pct_changes else 0
    median_monthly_pct = float(np.median(monthly_pct_changes)) if monthly_pct_changes else 0
    pos_months = sum(1 for x in monthly_pct_changes if x > 0)
    neg_months = sum(1 for x in monthly_pct_changes if x < 0)
    zero_months = sum(1 for x in monthly_pct_changes if x == 0)

    # TOP 5 best/worst
    traded_rows = [r for r in rows if r["trades"] > 0]
    best = sorted(traded_rows, key=lambda r: -r["pv_change"])[:5]
    worst = sorted(traded_rows, key=lambda r: r["pv_change"])[:5]

    # ASCII bar scale
    max_abs = max((abs(r["pv_change"]) for r in rows), default=1)

    # 리포트 작성
    L = []
    L.append("=" * 95)
    L.append("  v2_dante_A 월별 P&L 상세 분석")
    L.append(f"  기간: 2021-04 ~ 2026-04 (61개월)  /  생성: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"  초기자본: {initial_capital:,.0f}원")
    L.append("=" * 95)

    # 요약
    final_pv = rows[-1]["end_pv"]
    final_cum = rows[-1]["cum_ret_pct"]
    L.append("")
    L.append("[요약]")
    L.append(f"  최종 자산         : {final_pv:,.0f}원  (누적 {final_cum:+.2f}%)")
    L.append(f"  거래 있었던 월    : {len(traded_rows)} / {len(rows)} 개월")
    L.append(f"  수익월 / 손실월   : {pos_months} / {neg_months}  (무변동 {zero_months})")
    L.append(f"  평균 월 수익률    : {avg_monthly_pct:+.2f}% (중앙값 {median_monthly_pct:+.2f}%)")
    L.append(f"  최대 연속 수익월  : {max_up} ({up_span[0][:4]}-{up_span[0][4:]} ~ {up_span[1][:4]}-{up_span[1][4:]})" if max_up else "")
    L.append(f"  최대 연속 손실월  : {max_down} ({down_span[0][:4]}-{down_span[0][4:]} ~ {down_span[1][:4]}-{down_span[1][4:]})" if max_down else "")

    # TOP 5
    L.append("")
    L.append("[TOP 5 BEST 월]")
    L.append(f"  {'월':<9} {'거래':>5} {'승률':>8} {'PV 증감':>14} {'월말 PV':>14} {'누적%':>9}")
    for r in best:
        wr = f'{r["win_rate"]:.1f}%' if r["win_rate"] is not None else "-"
        L.append(
            f'  {r["ym"][:4]}-{r["ym"][4:]} {r["trades"]:>5} {wr:>8} '
            f'{r["pv_change"]:>+14,.0f} {r["end_pv"]:>14,.0f} {r["cum_ret_pct"]:>+8.2f}%'
        )

    L.append("")
    L.append("[TOP 5 WORST 월]")
    L.append(f"  {'월':<9} {'거래':>5} {'승률':>8} {'PV 증감':>14} {'월말 PV':>14} {'누적%':>9}")
    for r in worst:
        wr = f'{r["win_rate"]:.1f}%' if r["win_rate"] is not None else "-"
        L.append(
            f'  {r["ym"][:4]}-{r["ym"][4:]} {r["trades"]:>5} {wr:>8} '
            f'{r["pv_change"]:>+14,.0f} {r["end_pv"]:>14,.0f} {r["cum_ret_pct"]:>+8.2f}%'
        )

    # 전체 월별 표
    L.append("")
    L.append("[전체 월별 표] (61개월)")
    L.append(
        f'  {"월":<9} {"거래":>4} {"승률":>7} {"실현P&L":>13} {"PV 증감":>13} '
        f'{"월말 PV":>13} {"누적%":>8}   {"차트 (-)│(+)"}'
    )
    L.append("-" * 115)
    for r in rows:
        if r["trades"] == 0:
            mark = "(매매없음)"
            wr = "-"
            pnl_s = "-"
            tr_s = "0"
        else:
            mark = ""
            wr = f'{r["win_rate"]:.1f}%'
            pnl_s = f'{r["pnl"]:+,.0f}'
            tr_s = f'{r["trades"]}'
        b = bar(r["pv_change"], max_abs, width=20)
        L.append(
            f'  {r["ym"][:4]}-{r["ym"][4:]} {tr_s:>4} {wr:>7} {pnl_s:>13} '
            f'{r["pv_change"]:>+13,.0f} {r["end_pv"]:>13,.0f} {r["cum_ret_pct"]:>+7.2f}% '
            f'  {b} {mark}'
        )

    # 연도 소계
    L.append("")
    L.append("[연도별 소계]")
    by_year: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "end_pv": 0.0, "start_pv": 0.0})
    year_prev_pv = initial_capital
    for r in rows:
        y = r["ym"][:4]
        by_year[y]["trades"] += r["trades"]
        by_year[y]["wins"] += r["wins"]
        by_year[y]["pnl"] += r["pnl"]
        by_year[y]["end_pv"] = r["end_pv"]
        if by_year[y]["start_pv"] == 0:
            by_year[y]["start_pv"] = year_prev_pv
        year_prev_pv = r["end_pv"]
    L.append(f'  {"연도":<6} {"거래":>6} {"승률":>8} {"실현P&L":>15} {"년초→년말":>22} {"연 수익률":>10}')
    L.append("-" * 80)
    for y, d in sorted(by_year.items()):
        wr = (d["wins"] / d["trades"] * 100) if d["trades"] else 0
        yret = (d["end_pv"] - d["start_pv"]) / d["start_pv"] * 100 if d["start_pv"] else 0
        L.append(
            f'  {y:<6} {d["trades"]:>6} {wr:>7.2f}% {d["pnl"]:>+15,.0f} '
            f'{d["start_pv"]:>10,.0f} → {d["end_pv"]:>9,.0f} {yret:>+9.2f}%'
        )

    OUT_TXT.write_text("\n".join(L), encoding="utf-8")
    bt.log.info(f"저장: {OUT_TXT.name} / 총 {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
