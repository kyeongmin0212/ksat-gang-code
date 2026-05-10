"""v2_dante_A 최종 확정 스펙(1:2:4:8 순수) 월별 P&L 분석.

기존 .py 파일 수정 없음. backtesting 모듈 read-only import.
결과 저장: logs/monthly_pnl_v2_dante_A.txt  (요청 경로)
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt  # noqa

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT_TXT = BASE / "logs" / "monthly_pnl_v2_dante_A.txt"


def configure() -> None:
    """v2_dante_A 최종 확정 스펙."""
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"
    bt.CONFIG["initial_capital"] = 10_000_000
    bt.CONFIG["max_per_stock"] = 1_000_000

    # 분할매수 1:2:4:8 (총 15단위) × 100만원
    bud = bt.CONFIG["max_per_stock"]
    bt.CONFIG["stage_amounts"] = [
        bud * 1 // 15,
        bud * 2 // 15,
        bud * 4 // 15,
        bud - (bud * 1 // 15 + bud * 2 // 15 + bud * 4 // 15),
    ]

    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False
    bt.CONFIG["exclude_preferred_stocks"] = True
    bt.CONFIG["allowed_position_types"] = ["중장기"]

    # v5/v6 flags off
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False
    bt.CONFIG["enable_trailing_stop"] = False
    bt.CONFIG["target_strategy"] = "median"
    bt.CONFIG["simple_stop_loss_pct"] = None


def apply_local_uC_filter(df):
    mask = ~df["종목명"].astype(str).str.endswith("우C")
    removed = int((~mask).sum())
    if removed:
        bt.log.info(f"[로컬] '우C' 끝 종목 {removed} 행 추가 제거")
    return df[mask].reset_index(drop=True)


def month_end_pv(pv_history: list[tuple[str, float]]) -> dict[str, float]:
    by_month: dict[str, tuple[str, float]] = {}
    for ds, pv in pv_history:
        m = ds[:6]
        prev = by_month.get(m)
        if prev is None or ds > prev[0]:
            by_month[m] = (ds, pv)
    return {m: pv for m, (_, pv) in sorted(by_month.items())}


def month_iter(start_ym: str, end_ym: str):
    y, m = int(start_ym[:4]), int(start_ym[4:6])
    ey, em = int(end_ym[:4]), int(end_ym[4:6])
    while (y, m) <= (ey, em):
        yield f"{y:04d}{m:02d}"
        m += 1
        if m > 12:
            m = 1
            y += 1


def bar(pnl: float, max_abs: float, width: int = 20) -> str:
    if max_abs <= 0:
        return " " * width + "│" + " " * width
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
    bt.log.info("v2_dante_A 최종 확정 — 월별 P&L 재시뮬")
    bt.log.info(f"stage_amounts={bt.CONFIG['stage_amounts']}")

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    df = apply_local_uC_filter(df)
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)
    kospi_regime = bt.load_kospi_regime(
        bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG
    )
    trades, pv_history, _meta = bt.simulate(df, bt.CONFIG, kospi_regime)
    del df

    bt.log.info(f"시뮬 완료 / {time.time()-t0:.1f}s / 거래 {len(trades):,}건")

    initial_capital = float(bt.CONFIG["initial_capital"])

    # 월별 거래 집계 (close_date 기준)
    by_ym: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        ds = t.close_date or t.open_date
        ym = ds[:6]
        d = by_ym[ym]
        d["trades"] += 1
        if t.realized_pnl() > 0:
            d["wins"] += 1
        d["pnl"] += t.realized_pnl()

    pv_by_month = month_end_pv(pv_history)

    months = list(month_iter("202104", "202604"))

    rows = []
    prev_pv = initial_capital
    for m in months:
        tr = by_ym.get(m, {"trades": 0, "wins": 0, "pnl": 0.0})
        end_pv = pv_by_month.get(m, prev_pv)
        pv_change = end_pv - prev_pv
        cum_ret = (end_pv - initial_capital) / initial_capital * 100
        wr = (tr["wins"] / tr["trades"] * 100) if tr["trades"] else None
        rows.append({
            "ym": m,
            "trades": tr["trades"],
            "wins": tr["wins"],
            "win_rate": wr,
            "pnl": tr["pnl"],
            "end_pv": end_pv,
            "pv_change": pv_change,
            "cum_ret_pct": cum_ret,
        })
        prev_pv = end_pv

    # 연속 스트릭 (PV 증감 기준, 매매 없는 달은 리셋 아님)
    max_up = max_dn = 0
    cur_up = cur_dn = 0
    up_span = ("", "", 0)
    dn_span = ("", "", 0)
    for i, r in enumerate(rows):
        if r["trades"] == 0:
            continue
        if r["pv_change"] > 0:
            cur_up += 1
            cur_dn = 0
            if cur_up > max_up:
                max_up = cur_up
                start = rows[i - cur_up + 1]["ym"]
                up_span = (start, r["ym"], cur_up)
        elif r["pv_change"] < 0:
            cur_dn += 1
            cur_up = 0
            if cur_dn > max_dn:
                max_dn = cur_dn
                start = rows[i - cur_dn + 1]["ym"]
                dn_span = (start, r["ym"], cur_dn)
        else:
            cur_up = cur_dn = 0

    # 월별 비율 변화
    pct_changes = []
    prev = initial_capital
    for r in rows:
        if prev > 0:
            pct_changes.append((r["end_pv"] - prev) / prev * 100)
        prev = r["end_pv"]
    avg_mpct = float(np.mean(pct_changes)) if pct_changes else 0
    med_mpct = float(np.median(pct_changes)) if pct_changes else 0
    pos_m = sum(1 for x in pct_changes if x > 0)
    neg_m = sum(1 for x in pct_changes if x < 0)
    zero_m = sum(1 for x in pct_changes if x == 0)

    # TOP 5
    traded_rows = [r for r in rows if r["trades"] > 0]
    best5 = sorted(traded_rows, key=lambda r: -r["pv_change"])[:5]
    worst5 = sorted(traded_rows, key=lambda r: r["pv_change"])[:5]

    max_abs = max((abs(r["pv_change"]) for r in rows), default=1) or 1

    # ------------------------------ 리포트 ------------------------------
    L = []
    L.append("=" * 100)
    L.append("  v2_dante_A (최종 확정 스펙) 월별 P&L 상세 분석")
    L.append(f"  기간: 2021-04 ~ 2026-04 (61개월)  /  생성: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"  초기자본: {initial_capital:,.0f}원  /  stage_amounts: {bt.CONFIG['stage_amounts']}")
    L.append("=" * 100)

    final_pv = rows[-1]["end_pv"]
    final_cum = rows[-1]["cum_ret_pct"]
    L.append("")
    L.append("[요약]")
    L.append(f"  최종 자산         : {final_pv:,.0f}원  (누적 {final_cum:+.2f}%)")
    L.append(f"  거래 있었던 월    : {len(traded_rows)} / {len(rows)} 개월")
    L.append(f"  수익월/손실월/무변동 : {pos_m} / {neg_m} / {zero_m}")
    L.append(f"  평균 월 수익률    : {avg_mpct:+.2f}% (중앙값 {med_mpct:+.2f}%)")
    if max_up:
        L.append(f"  최대 연속 수익월  : {max_up} ({up_span[0][:4]}-{up_span[0][4:]} ~ {up_span[1][:4]}-{up_span[1][4:]})")
    if max_dn:
        L.append(f"  최대 연속 손실월  : {max_dn} ({dn_span[0][:4]}-{dn_span[0][4:]} ~ {dn_span[1][:4]}-{dn_span[1][4:]})")

    L.append("")
    L.append("[TOP 5 BEST 월]")
    L.append(f"  {'월':<9} {'거래':>5} {'승률':>8} {'PV 증감':>14} {'월말 PV':>14} {'누적%':>9}")
    for r in best5:
        wr = f'{r["win_rate"]:.1f}%' if r["win_rate"] is not None else "-"
        L.append(
            f'  {r["ym"][:4]}-{r["ym"][4:]} {r["trades"]:>5} {wr:>8} '
            f'{r["pv_change"]:>+14,.0f} {r["end_pv"]:>14,.0f} {r["cum_ret_pct"]:>+8.2f}%'
        )

    L.append("")
    L.append("[TOP 5 WORST 월]")
    L.append(f"  {'월':<9} {'거래':>5} {'승률':>8} {'PV 증감':>14} {'월말 PV':>14} {'누적%':>9}")
    for r in worst5:
        wr = f'{r["win_rate"]:.1f}%' if r["win_rate"] is not None else "-"
        L.append(
            f'  {r["ym"][:4]}-{r["ym"][4:]} {r["trades"]:>5} {wr:>8} '
            f'{r["pv_change"]:>+14,.0f} {r["end_pv"]:>14,.0f} {r["cum_ret_pct"]:>+8.2f}%'
        )

    L.append("")
    L.append("[전체 월별 표] (61개월)")
    L.append(
        f'  {"월":<9} {"거래":>4} {"승률":>7} {"실현P&L":>13} {"PV 증감":>13} '
        f'{"월말 PV":>13} {"누적%":>8}   차트  (-)│(+)'
    )
    L.append("-" * 120)
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
            f'{r["pv_change"]:>+13,.0f} {r["end_pv"]:>13,.0f} {r["cum_ret_pct"]:>+7.2f}%  '
            f'  {b} {mark}'
        )

    L.append("")
    L.append("[연도별 소계]")
    by_year: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0,
                                                     "end_pv": 0.0, "start_pv": 0.0})
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
    L.append(f'  {"연도":<6} {"거래":>6} {"승률":>8} {"실현P&L":>15} {"년초→년말":>25} {"연 수익률":>10}')
    L.append("-" * 85)
    for y, d in sorted(by_year.items()):
        wr = (d["wins"] / d["trades"] * 100) if d["trades"] else 0
        yret = ((d["end_pv"] - d["start_pv"]) / d["start_pv"] * 100) if d["start_pv"] else 0
        L.append(
            f'  {y:<6} {d["trades"]:>6} {wr:>7.2f}% {d["pnl"]:>+15,.0f} '
            f'{d["start_pv"]:>11,.0f} → {d["end_pv"]:>10,.0f} {yret:>+9.2f}%'
        )

    OUT_TXT.write_text("\n".join(L), encoding="utf-8")
    bt.log.info(f"저장: {OUT_TXT.name} / 총 {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
