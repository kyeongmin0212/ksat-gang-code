"""v2 전략 심층 분석 — 10개 항목.

- v2 = 점수 4점 + KOSPI 200MA 약세장 필터
- 기간: 2021-04-23 ~ 2026-04-22 (5년)
- 입력: stock_data.db (실시간 재시뮬)
- 출력:
    · backtest_v2_deep_analysis.json (구조화 데이터)
    · backtest_v2_report.txt (사람이 읽기 쉬운 리포트)
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import json
import gc
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt  # noqa

BASE_DIR = Path(r"C:\Users\sji48\ksat_gang")
OUT_JSON = BASE_DIR / "backtest_v2_deep_analysis.json"
OUT_TXT = BASE_DIR / "backtest_v2_report.txt"


# ============================================================
# v2 설정 고정
# ============================================================
def configure_v2() -> None:
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False


# ============================================================
# 분석 유틸
# ============================================================
def month_of(ds: str) -> str:
    return ds[:6]  # YYYYMM


def business_days_between(a: str, b: str, trading_dates: list[str]) -> int:
    try:
        return trading_dates.index(b) - trading_dates.index(a)
    except ValueError:
        # fallback: 달력일
        da = datetime.strptime(a, "%Y%m%d")
        db = datetime.strptime(b, "%Y%m%d")
        return max(0, (db - da).days)


def terminal_reason(pos: bt.Position) -> str:
    """포지션의 최종 매도 사유 — 마지막 이벤트 기준."""
    if not pos.sell_events:
        return "미체결"
    return pos.sell_events[-1]["reason"]


def share_weighted_reason(pos: bt.Position) -> str:
    """매도 주수 비중이 가장 큰 사유."""
    if not pos.sell_events:
        return "미체결"
    by_reason: dict[str, int] = {}
    for ev in pos.sell_events:
        by_reason[ev["reason"]] = by_reason.get(ev["reason"], 0) + ev["shares"]
    return max(by_reason.items(), key=lambda x: x[1])[0]


def fmt_krw(x: float) -> str:
    return f"{x:,.0f}"


# ============================================================
# (1) 월별 성과
# ============================================================
def analyze_monthly(trades: list[bt.Position]) -> dict:
    by_month: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        m = month_of(t.close_date or t.open_date)
        d = by_month[m]
        d["trades"] += 1
        if t.realized_pnl() > 0:
            d["wins"] += 1
        d["pnl"] += t.realized_pnl()

    # 월번호(1~12)별 집계 (계절성)
    by_month_num: dict[int, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for m, d in by_month.items():
        mn = int(m[4:6])
        by_month_num[mn]["trades"] += d["trades"]
        by_month_num[mn]["wins"] += d["wins"]
        by_month_num[mn]["pnl"] += d["pnl"]

    return {
        "by_year_month": {
            m: {
                "trades": d["trades"],
                "wins": d["wins"],
                "win_rate_pct": round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0,
                "pnl_krw": round(d["pnl"], 0),
            }
            for m, d in sorted(by_month.items())
        },
        "by_calendar_month": {
            str(mn): {
                "trades": d["trades"],
                "wins": d["wins"],
                "win_rate_pct": round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0,
                "pnl_krw": round(d["pnl"], 0),
                "avg_pnl_per_trade_krw": round(d["pnl"] / d["trades"], 0) if d["trades"] else 0,
            }
            for mn, d in sorted(by_month_num.items())
        },
    }


# ============================================================
# (2) 보유 기간별
# ============================================================
def analyze_holding_period(trades: list[bt.Position], trading_dates: list[str]) -> dict:
    winners_hold = []
    losers_hold = []
    timeout_pnls = []
    hold_days_all = []
    for t in trades:
        if not t.close_date:
            continue
        hold = business_days_between(t.open_date, t.close_date, trading_dates)
        hold_days_all.append(hold)
        pnl = t.realized_pnl_pct()
        if pnl > 0:
            winners_hold.append(hold)
        else:
            losers_hold.append(hold)
        if terminal_reason(t) == "기간초과":
            timeout_pnls.append(pnl)

    return {
        "avg_hold_winners": round(float(np.mean(winners_hold)), 2) if winners_hold else 0,
        "avg_hold_losers": round(float(np.mean(losers_hold)), 2) if losers_hold else 0,
        "avg_hold_all": round(float(np.mean(hold_days_all)), 2) if hold_days_all else 0,
        "median_hold_all": int(np.median(hold_days_all)) if hold_days_all else 0,
        "timeout_trade_count": len(timeout_pnls),
        "timeout_avg_return_pct": round(float(np.mean(timeout_pnls)), 2) if timeout_pnls else 0,
        "timeout_win_rate_pct": round(
            100 * sum(1 for p in timeout_pnls if p > 0) / len(timeout_pnls), 2
        ) if timeout_pnls else 0,
    }


# ============================================================
# (3) 매도 이유별 분석
# ============================================================
def analyze_exit_reasons(trades: list[bt.Position]) -> dict:
    by_reason: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        r = share_weighted_reason(t)
        by_reason[r].append(t.realized_pnl_pct())

    total = len(trades) or 1
    out: dict[str, dict] = {}
    for r, returns in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        wins = [x for x in returns if x > 0]
        out[r] = {
            "count": len(returns),
            "share_pct": round(100 * len(returns) / total, 2),
            "win_rate_pct": round(100 * len(wins) / len(returns), 2) if returns else 0,
            "avg_return_pct": round(float(np.mean(returns)), 2),
            "median_return_pct": round(float(np.median(returns)), 2),
            "max_return_pct": round(float(np.max(returns)), 2) if returns else 0,
            "min_return_pct": round(float(np.min(returns)), 2) if returns else 0,
        }
    return out


# ============================================================
# (4) 연속 손실/수익 스트릭
# ============================================================
def analyze_streaks(trades: list[bt.Position]) -> dict:
    # close_date 순 정렬
    ordered = sorted(trades, key=lambda x: (x.close_date or x.open_date))
    max_win_streak = 0
    max_loss_streak = 0
    cur_win = 0
    cur_loss = 0
    win_streak_start = loss_streak_start = None
    longest_win_span = ("", "", 0)
    longest_loss_span = ("", "", 0)

    for t in ordered:
        date = t.close_date or t.open_date
        if t.realized_pnl() > 0:
            if cur_win == 0:
                win_streak_start = date
            cur_win += 1
            cur_loss = 0
            if cur_win > max_win_streak:
                max_win_streak = cur_win
                longest_win_span = (win_streak_start, date, cur_win)
        else:
            if cur_loss == 0:
                loss_streak_start = date
            cur_loss += 1
            cur_win = 0
            if cur_loss > max_loss_streak:
                max_loss_streak = cur_loss
                longest_loss_span = (loss_streak_start, date, cur_loss)

    return {
        "max_consecutive_wins": max_win_streak,
        "max_consecutive_wins_span": {
            "from": longest_win_span[0], "to": longest_win_span[1], "count": longest_win_span[2],
        },
        "max_consecutive_losses": max_loss_streak,
        "max_consecutive_losses_span": {
            "from": longest_loss_span[0], "to": longest_loss_span[1], "count": longest_loss_span[2],
        },
    }


# ============================================================
# (5) 포지션별 세부
# ============================================================
def analyze_position_types(trades: list[bt.Position], trading_dates: list[str]) -> dict:
    by_pt: dict[str, list[bt.Position]] = defaultdict(list)
    for t in trades:
        by_pt[t.position_type].append(t)

    out: dict[str, dict] = {}
    for pt, ts in by_pt.items():
        returns = [t.realized_pnl_pct() for t in ts]
        pnls = [t.realized_pnl() for t in ts]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        holds = [business_days_between(t.open_date, t.close_date or t.open_date, trading_dates) for t in ts]
        out[pt] = {
            "count": len(ts),
            "win_rate_pct": round(100 * len(wins) / len(ts), 2) if ts else 0,
            "avg_return_pct": round(float(np.mean(returns)), 2),
            "avg_win_pct": round(float(np.mean(wins)), 2) if wins else 0,
            "avg_loss_pct": round(float(np.mean(losses)), 2) if losses else 0,
            "total_pnl_krw": round(sum(pnls), 0),
            "avg_pnl_per_trade_krw": round(sum(pnls) / len(ts), 0),
            "avg_hold_days": round(float(np.mean(holds)), 2) if holds else 0,
            "profit_factor": round(sum(p for p in pnls if p > 0) / abs(sum(p for p in pnls if p < 0)), 2)
                if any(p < 0 for p in pnls) else 0,
        }
    return out


# ============================================================
# (6) 동시 보유 종목 수
# ============================================================
def analyze_concurrent_positions(
    trades: list[bt.Position], trading_dates: list[str], initial_capital: float
) -> dict:
    # 각 거래일의 오픈 중 포지션 수 카운트
    date_idx = {d: i for i, d in enumerate(trading_dates)}
    counter = np.zeros(len(trading_dates), dtype=int)
    invested = np.zeros(len(trading_dates), dtype=float)
    for t in trades:
        oi = date_idx.get(t.open_date)
        ci = date_idx.get(t.close_date or t.open_date)
        if oi is None or ci is None:
            continue
        counter[oi:ci + 1] += 1
        invested[oi:ci + 1] += t.total_cost

    return {
        "max_concurrent": int(counter.max()) if len(counter) else 0,
        "avg_concurrent": round(float(counter.mean()), 2) if len(counter) else 0,
        "median_concurrent": int(np.median(counter)) if len(counter) else 0,
        "max_invested_krw": round(float(invested.max()), 0) if len(invested) else 0,
        "avg_invested_krw": round(float(invested.mean()), 0) if len(invested) else 0,
        "max_capital_util_pct": round(float(invested.max()) / initial_capital * 100, 2) if len(invested) else 0,
        "avg_capital_util_pct": round(float(invested.mean()) / initial_capital * 100, 2) if len(invested) else 0,
    }


# ============================================================
# (7) MDD 구간 상세
# ============================================================
def analyze_drawdown(pv_history: list[tuple[str, float]]) -> dict:
    if not pv_history:
        return {}
    dates = [d for d, _ in pv_history]
    pv = np.array([v for _, v in pv_history], dtype=float)
    cummax = np.maximum.accumulate(pv)
    dd = (pv - cummax) / cummax  # 음수

    trough_idx = int(np.argmin(dd))
    mdd = float(dd[trough_idx])
    # peak: trough 이전의 cummax 값이 pv와 같아지는 직전 지점
    peak_pv = cummax[trough_idx]
    peak_idx = int(np.where(pv[: trough_idx + 1] == peak_pv)[0][0])

    # 회복 지점: trough 이후 pv가 peak_pv 이상으로 복귀하는 최초 시점
    recovery_idx = None
    for i in range(trough_idx, len(pv)):
        if pv[i] >= peak_pv:
            recovery_idx = i
            break

    dur_dd_days = int(
        (datetime.strptime(dates[trough_idx], "%Y%m%d") - datetime.strptime(dates[peak_idx], "%Y%m%d")).days
    )
    if recovery_idx is not None:
        dur_recovery_days = int(
            (datetime.strptime(dates[recovery_idx], "%Y%m%d") - datetime.strptime(dates[trough_idx], "%Y%m%d")).days
        )
    else:
        dur_recovery_days = -1  # 미회복

    return {
        "mdd_pct": round(mdd * 100, 2),
        "peak_date": dates[peak_idx],
        "peak_pv": round(float(peak_pv), 0),
        "trough_date": dates[trough_idx],
        "trough_pv": round(float(pv[trough_idx]), 0),
        "dd_duration_days": dur_dd_days,
        "recovery_date": dates[recovery_idx] if recovery_idx is not None else None,
        "recovery_duration_days": dur_recovery_days,
        "recovered": recovery_idx is not None,
    }


# ============================================================
# (8) 승률 vs 손익비
# ============================================================
def analyze_win_loss_ratio(trades: list[bt.Position]) -> dict:
    returns = [t.realized_pnl_pct() for t in trades]
    pnls = [t.realized_pnl() for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    avg_win = float(np.mean(wins)) if wins else 0
    avg_loss = float(np.mean(losses)) if losses else 0
    win_rate = len(wins) / len(trades) if trades else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    # Kelly-like 기대값: p*avg_win + (1-p)*avg_loss
    expected_pct = win_rate * avg_win + (1 - win_rate) * avg_loss

    # 최소 생존 승률 (avg_win/avg_loss 그대로일 때, 기대값 0이 되는 승률)
    # p*w + (1-p)*l = 0 → p = |l| / (w + |l|)
    break_even_wr = abs(avg_loss) / (avg_win + abs(avg_loss)) if (avg_win + abs(avg_loss)) else 0

    return {
        "win_rate_pct": round(win_rate * 100, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "payoff_ratio": round(payoff, 2),
        "expected_value_per_trade_pct": round(expected_pct, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        "gross_profit_krw": round(gross_profit, 0),
        "gross_loss_krw": round(-gross_loss, 0),
        "break_even_win_rate_pct": round(break_even_wr * 100, 2),
        "margin_over_break_even_pct": round((win_rate - break_even_wr) * 100, 2),
    }


# ============================================================
# (9) 종목별 TOP 20
# ============================================================
def analyze_ticker_top(trades: list[bt.Position], n: int = 20) -> dict:
    per_ticker: dict[str, dict] = {}
    for t in trades:
        d = per_ticker.setdefault(
            t.ticker,
            {"ticker": t.ticker, "name": t.name, "trades": 0,
             "total_pnl": 0.0, "total_cost": 0.0, "wins": 0},
        )
        d["trades"] += 1
        d["total_pnl"] += t.realized_pnl()
        d["total_cost"] += t.total_cost
        if t.realized_pnl() > 0:
            d["wins"] += 1

    for d in per_ticker.values():
        d["avg_return_pct"] = (
            round(d["total_pnl"] / d["total_cost"] * 100, 2) if d["total_cost"] > 0 else 0
        )
        d["win_rate_pct"] = round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0
        d["total_pnl_krw"] = round(d["total_pnl"], 0)
        d["total_cost_krw"] = round(d["total_cost"], 0)
        del d["total_pnl"], d["total_cost"]

    by_pnl = sorted(per_ticker.values(), key=lambda x: -x["total_pnl_krw"])
    return {
        "winners": by_pnl[:n],
        "losers": by_pnl[-n:][::-1],
    }


# ============================================================
# 리포트 포맷팅
# ============================================================
def format_report(analysis: dict, meta: dict) -> str:
    L = []
    L.append("=" * 80)
    L.append("  v2 전략 심층 분석 리포트")
    L.append("  기간: " + meta["period"] + "  /  전략: 점수 4점 + KOSPI 200MA 약세장 필터")
    L.append("=" * 80)

    ov = meta.get("overall", {})
    L.append("")
    L.append("▶ 기본 성과")
    L.append(f"  총 거래       : {ov.get('total_trades', 0):,} 건")
    L.append(f"  승률          : {ov.get('win_rate_pct', 0):.2f}%")
    L.append(f"  누적 수익률   : {ov.get('cumulative_return_pct', 0):.2f}%")
    L.append(f"  CAGR          : {ov.get('cagr_pct', 0):.2f}%")
    L.append(f"  MDD           : {ov.get('mdd_pct', 0):.2f}%")
    L.append(f"  Sharpe        : {ov.get('sharpe', 0):.2f}")
    L.append(f"  초기→최종     : {ov.get('initial_capital', 0):,.0f}원 → {ov.get('final_portfolio_value', 0):,.0f}원")

    # (1) 월별
    L.append("")
    L.append("=" * 80)
    L.append("[1] 월별 성과")
    L.append("=" * 80)
    L.append("")
    L.append("── YYYY-MM 별 실적 ──")
    L.append(f"{'월':<9} {'거래':>5} {'승률':>7} {'P&L(원)':>15}")
    L.append("-" * 45)
    for m, r in analysis["monthly"]["by_year_month"].items():
        L.append(f'{m[:4]}-{m[4:6]:>2} {r["trades"]:>5} {r["win_rate_pct"]:>6.2f}% {r["pnl_krw"]:>15,.0f}')

    L.append("")
    L.append("── 캘린더 월별 (1~12) 계절성 ──")
    L.append(f"{'월':<4} {'거래':>5} {'승률':>7} {'총 P&L':>14} {'거래당 평균':>14}")
    L.append("-" * 55)
    for mn, r in analysis["monthly"]["by_calendar_month"].items():
        L.append(f'{mn:<4} {r["trades"]:>5} {r["win_rate_pct"]:>6.2f}% {r["pnl_krw"]:>14,.0f} {r["avg_pnl_per_trade_krw"]:>14,.0f}')

    # (2) 보유 기간
    h = analysis["holding"]
    L.append("")
    L.append("=" * 80)
    L.append("[2] 보유 기간 분석 (영업일 기준)")
    L.append("=" * 80)
    L.append(f"  전체 평균 보유    : {h['avg_hold_all']} 일 (중앙값 {h['median_hold_all']} 일)")
    L.append(f"  수익 거래 평균    : {h['avg_hold_winners']} 일")
    L.append(f"  손실 거래 평균    : {h['avg_hold_losers']} 일")
    L.append(f"  강제 청산 (기간초과) : {h['timeout_trade_count']} 건")
    L.append(f"    └ 평균 수익률    : {h['timeout_avg_return_pct']}%")
    L.append(f"    └ 승률          : {h['timeout_win_rate_pct']}%")

    # (3) 매도 이유
    L.append("")
    L.append("=" * 80)
    L.append("[3] 매도 이유별 분석 (주수 가중 1순위)")
    L.append("=" * 80)
    L.append(f"{'사유':<12} {'건수':>5} {'비중':>6} {'승률':>7} {'평균':>8} {'중앙값':>8} {'최대':>8} {'최소':>8}")
    L.append("-" * 75)
    for r, d in analysis["exit_reasons"].items():
        L.append(
            f'{r:<12} {d["count"]:>5} {d["share_pct"]:>5.2f}% {d["win_rate_pct"]:>6.2f}% '
            f'{d["avg_return_pct"]:>7.2f}% {d["median_return_pct"]:>7.2f}% '
            f'{d["max_return_pct"]:>7.2f}% {d["min_return_pct"]:>7.2f}%'
        )

    # (4) 스트릭
    s = analysis["streaks"]
    L.append("")
    L.append("=" * 80)
    L.append("[4] 연속 손실/수익 분석")
    L.append("=" * 80)
    L.append(f"  최대 연승 : {s['max_consecutive_wins']} 회 "
             f"({s['max_consecutive_wins_span']['from']} ~ {s['max_consecutive_wins_span']['to']})")
    L.append(f"  최대 연패 : {s['max_consecutive_losses']} 회 "
             f"({s['max_consecutive_losses_span']['from']} ~ {s['max_consecutive_losses_span']['to']})")

    # (5) 포지션별
    L.append("")
    L.append("=" * 80)
    L.append("[5] 포지션 타입별 세부")
    L.append("=" * 80)
    L.append(f"{'타입':<14} {'거래':>5} {'승률':>7} {'평균수익':>9} {'평균승':>7} {'평균손':>7} {'보유일':>7} {'PF':>5} {'누적 P&L':>13}")
    L.append("-" * 95)
    for pt, d in analysis["position_types"].items():
        L.append(
            f'{pt:<14} {d["count"]:>5} {d["win_rate_pct"]:>6.2f}% '
            f'{d["avg_return_pct"]:>8.2f}% {d["avg_win_pct"]:>6.2f}% '
            f'{d["avg_loss_pct"]:>6.2f}% {d["avg_hold_days"]:>6.1f} '
            f'{d["profit_factor"]:>5.2f} {d["total_pnl_krw"]:>13,.0f}'
        )

    # (6) 동시 보유
    c = analysis["concurrent"]
    L.append("")
    L.append("=" * 80)
    L.append("[6] 동시 보유 / 자본 활용도")
    L.append("=" * 80)
    L.append(f"  최대 동시 보유    : {c['max_concurrent']} 종목")
    L.append(f"  평균 동시 보유    : {c['avg_concurrent']} 종목 (중앙값 {c['median_concurrent']})")
    L.append(f"  최대 투입 금액    : {c['max_invested_krw']:,.0f}원")
    L.append(f"  평균 투입 금액    : {c['avg_invested_krw']:,.0f}원")
    L.append(f"  최대 자본 활용도  : {c['max_capital_util_pct']}%")
    L.append(f"  평균 자본 활용도  : {c['avg_capital_util_pct']}%")

    # (7) MDD 상세
    d = analysis["drawdown"]
    L.append("")
    L.append("=" * 80)
    L.append("[7] 최악의 구간 (MDD) 상세")
    L.append("=" * 80)
    L.append(f"  MDD              : {d.get('mdd_pct', 0)}%")
    L.append(f"  Peak 날짜/PV      : {d.get('peak_date')}  /  {d.get('peak_pv', 0):,.0f}원")
    L.append(f"  Trough 날짜/PV    : {d.get('trough_date')}  /  {d.get('trough_pv', 0):,.0f}원")
    L.append(f"  Peak→Trough      : {d.get('dd_duration_days')} 일")
    if d.get("recovered"):
        L.append(f"  Recovery 날짜     : {d.get('recovery_date')}")
        L.append(f"  Trough→Recovery  : {d.get('recovery_duration_days')} 일")
    else:
        L.append("  Recovery         : 아직 미회복 (종료 시점까지)")

    # (8) 손익비
    w = analysis["win_loss"]
    L.append("")
    L.append("=" * 80)
    L.append("[8] 승률 vs 손익비")
    L.append("=" * 80)
    L.append(f"  승률               : {w['win_rate_pct']}%")
    L.append(f"  평균 승리 수익     : {w['avg_win_pct']}%")
    L.append(f"  평균 손실          : {w['avg_loss_pct']}%")
    L.append(f"  손익비 (Payoff)    : {w['payoff_ratio']} (평균승/평균손)")
    L.append(f"  Profit Factor      : {w['profit_factor']} (총수익/총손실)")
    L.append(f"  거래당 기대값      : {w['expected_value_per_trade_pct']}%")
    L.append(f"  손익분기 승률      : {w['break_even_win_rate_pct']}%")
    L.append(f"  여유 승률          : {w['margin_over_break_even_pct']}%p")
    L.append(f"  총 수익            : {w['gross_profit_krw']:,.0f}원")
    L.append(f"  총 손실            : {w['gross_loss_krw']:,.0f}원")

    # (9) TOP 20
    L.append("")
    L.append("=" * 80)
    L.append("[9] TOP 20 Winners / Losers (종목별 누적)")
    L.append("=" * 80)
    L.append("── WINNERS ──")
    L.append(f"{'#':<3} {'종목':<22} {'거래':>5} {'승률':>7} {'평균수익':>9} {'P&L':>13}")
    L.append("-" * 75)
    for i, t in enumerate(analysis["top_tickers"]["winners"], 1):
        label = f'{t["ticker"]} {t["name"]}'
        L.append(
            f'{i:<3} {label:<22} {t["trades"]:>5} {t["win_rate_pct"]:>6.2f}% '
            f'{t["avg_return_pct"]:>8.2f}% {t["total_pnl_krw"]:>13,.0f}'
        )
    L.append("")
    L.append("── LOSERS ──")
    L.append(f"{'#':<3} {'종목':<22} {'거래':>5} {'승률':>7} {'평균수익':>9} {'P&L':>13}")
    L.append("-" * 75)
    for i, t in enumerate(analysis["top_tickers"]["losers"], 1):
        label = f'{t["ticker"]} {t["name"]}'
        L.append(
            f'{i:<3} {label:<22} {t["trades"]:>5} {t["win_rate_pct"]:>6.2f}% '
            f'{t["avg_return_pct"]:>8.2f}% {t["total_pnl_krw"]:>13,.0f}'
        )

    # (10) 실전 주의사항
    L.append("")
    L.append("=" * 80)
    L.append("[10] 실전 적용 시 주의사항")
    L.append("=" * 80)
    for note in analysis["practical_notes"]:
        L.append(f"  • {note}")

    return "\n".join(L)


def build_practical_notes(analysis: dict, meta: dict) -> list[str]:
    notes = []
    ov = meta.get("overall", {})
    d = analysis["drawdown"]
    w = analysis["win_loss"]
    s = analysis["streaks"]
    h = analysis["holding"]

    if w["payoff_ratio"] < 2.0:
        notes.append(
            f"손익비({w['payoff_ratio']})가 2 미만 — 승률 하락 시 쉽게 적자 전환. "
            f"손익분기 승률 {w['break_even_win_rate_pct']}% 대비 여유 {w['margin_over_break_even_pct']}%p뿐"
        )
    if abs(d.get("mdd_pct", 0)) > 15:
        notes.append(
            f"MDD {d.get('mdd_pct',0)}% — 실계좌로는 공포 구간. "
            f"Peak {d.get('peak_date')} → Trough {d.get('trough_date')} ({d.get('dd_duration_days')}일 지속)"
        )
    if not d.get("recovered"):
        notes.append("MDD 구간이 아직 회복 안 된 상태로 백테스트 종료 — 실전에선 회복 지연 가능성 염두")
    if s["max_consecutive_losses"] >= 5:
        notes.append(
            f"최대 {s['max_consecutive_losses']}연패 경험 "
            f"({s['max_consecutive_losses_span']['from']} ~ {s['max_consecutive_losses_span']['to']}) "
            "— 심리적 흔들림 주의, 연패 중간에 규칙 이탈 금지"
        )
    if ov.get("sharpe", 0) < 1.0:
        notes.append(
            f"Sharpe {ov.get('sharpe',0)} < 1.0 — 변동성 대비 초과 수익 낮음. "
            "포지션 크기 상향보다 승률/손익비 개선에 우선 집중"
        )
    # 매도 이유
    er = analysis["exit_reasons"]
    if "기간초과" in er and er["기간초과"]["share_pct"] > 20:
        notes.append(
            f"기간초과 청산이 전체의 {er['기간초과']['share_pct']}% — "
            f"평균 {er['기간초과']['avg_return_pct']}%. 목표가·보유일 재검토 필요"
        )
    if "익절" in er and er["익절"]["share_pct"] < 30:
        notes.append(
            f"익절 비중 {er['익절']['share_pct']}% (30% 미만) — 목표가가 너무 멀거나 모멘텀 조기 소멸 신호"
        )
    # 포지션 타입
    pt = analysis["position_types"]
    worst = min(pt.items(), key=lambda x: x[1]["avg_return_pct"]) if pt else None
    if worst and worst[1]["avg_return_pct"] < 0:
        notes.append(
            f"{worst[0]} 포지션 평균 수익 {worst[1]['avg_return_pct']}% — "
            "해당 타입 진입 축소 또는 조건 강화 고려"
        )
    # 월별 계절성
    cm = analysis["monthly"]["by_calendar_month"]
    if cm:
        worst_m = min(cm.items(), key=lambda x: x[1]["avg_pnl_per_trade_krw"])
        best_m = max(cm.items(), key=lambda x: x[1]["avg_pnl_per_trade_krw"])
        notes.append(
            f"계절성: {worst_m[0]}월 가장 부진 (거래당 평균 {worst_m[1]['avg_pnl_per_trade_krw']:,.0f}원), "
            f"{best_m[0]}월 가장 호조 ({best_m[1]['avg_pnl_per_trade_krw']:,.0f}원) "
            "— 부진 월에 포지션 크기 축소 실험 여지"
        )
    # 자본 활용
    c = analysis["concurrent"]
    if c["max_capital_util_pct"] > 95:
        notes.append(
            f"자본 활용도 최고 {c['max_capital_util_pct']}% — "
            "현금 여유 거의 없음. 추가 급락 시 분할 매수 불가능한 구간 발생 가능"
        )

    notes.append(
        "생존 편향: 현재 상장 종목만 사용 — 상장폐지된 종목 포함 시 실제 성과 악화 가능"
    )
    notes.append(
        "KOSPI 시총 proxy 사용: 진짜 KOSPI 종합지수 대비 약세장 감지 타이밍 일부 오차 존재"
    )
    return notes


# ============================================================
# 메인
# ============================================================
def main() -> int:
    configure_v2()
    bt.log.info("=" * 70)
    bt.log.info("v2 심층 분석 시작")

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"])
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)

    kospi_regime = bt.load_kospi_regime(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    trades, pv_history, sim_meta = bt.simulate(df, bt.CONFIG, kospi_regime)

    stats = bt.compute_statistics(trades, pv_history, bt.CONFIG)
    trading_dates = sorted(df["날짜"].unique().tolist())
    del df
    gc.collect()

    bt.log.info(f"시뮬 완료 / {time.time()-t0:.1f}s / 거래 {len(trades):,}건 — 분석 시작")

    # 분석 수행
    analysis = {
        "monthly": analyze_monthly(trades),
        "holding": analyze_holding_period(trades, trading_dates),
        "exit_reasons": analyze_exit_reasons(trades),
        "streaks": analyze_streaks(trades),
        "position_types": analyze_position_types(trades, trading_dates),
        "concurrent": analyze_concurrent_positions(
            trades, trading_dates, bt.CONFIG["initial_capital"]
        ),
        "drawdown": analyze_drawdown(pv_history),
        "win_loss": analyze_win_loss_ratio(trades),
        "top_tickers": analyze_ticker_top(trades, n=20),
    }
    analysis["practical_notes"] = build_practical_notes(
        analysis,
        meta={"overall": stats.get("overall", {})},
    )

    # 저장
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "period": f'{bt.CONFIG["start_date"]} ~ {bt.CONFIG["end_date"]}',
            "strategy": "v2 (점수 4 + KOSPI 200MA 약세장 필터)",
            "overall": stats.get("overall", {}),
            "simulation_meta": sim_meta,
        },
        "analysis": analysis,
    }
    bt.save_json_atomic(payload, OUT_JSON)

    report = format_report(analysis, payload["meta"])
    OUT_TXT.write_text(report, encoding="utf-8")

    bt.log.info(f"저장: {OUT_JSON.name} + {OUT_TXT.name} / 총 {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
