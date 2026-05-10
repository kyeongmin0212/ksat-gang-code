"""v2_dante_A 최종 백테스트 + 상세 리포트.

조건 (확정)
- 점수 4점만 + KOSPI 200MA + 중장기만 + 우선주 제외
- 분할매수 1:2:4:8 / 자본 10M / 종목당 1M / 동시 보유 무제한
- 이중 손절 (1차 -1.5% 기준선 반매도 / 2차 구름하단 전량)
- 목표 = 3개 중간값
- 비용: 매수 0.015% / 매도 0.015% / 거래세 0.23% / 슬리피지 0.1%

출력
- backtest_results_v2_dante_A_final.json
- backtest_v2_dante_A_report.txt
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import json
import gc
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt  # noqa

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT_JSON = BASE / "backtest_results_v2_dante_A_final.json"
OUT_TXT = BASE / "backtest_v2_dante_A_report.txt"
CANDIDATES_WITH_PRICES = BASE / "candidates_with_prices.json"


def configure() -> None:
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False
    bt.CONFIG["exclude_preferred_stocks"] = True
    bt.CONFIG["allowed_position_types"] = ["중장기"]
    # v5/v6 off
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False
    bt.CONFIG["enable_trailing_stop"] = False
    bt.CONFIG["target_strategy"] = "median"
    bt.CONFIG["simple_stop_loss_pct"] = None


# ------------------------------ 분석 유틸 ------------------------------
def share_weighted_reason(pos: bt.Position) -> str:
    if not pos.sell_events:
        return "미체결"
    by_reason: dict[str, int] = {}
    for ev in pos.sell_events:
        by_reason[ev["reason"]] = by_reason.get(ev["reason"], 0) + ev["shares"]
    return max(by_reason.items(), key=lambda x: x[1])[0]


def analyze_exit_reasons(trades: list[bt.Position]) -> dict:
    by_reason: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_reason[share_weighted_reason(t)].append(t.realized_pnl_pct())
    total = len(trades) or 1
    out = {}
    for r, rets in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        wins = [x for x in rets if x > 0]
        out[r] = {
            "count": len(rets),
            "share_pct": round(100 * len(rets) / total, 2),
            "win_rate_pct": round(100 * len(wins) / len(rets), 2) if rets else 0,
            "avg_return_pct": round(float(np.mean(rets)), 2),
            "median_return_pct": round(float(np.median(rets)), 2),
            "max_return_pct": round(float(np.max(rets)), 2),
            "min_return_pct": round(float(np.min(rets)), 2),
        }
    return out


def analyze_monthly(trades: list[bt.Position]) -> dict:
    by_ym: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    by_cm: dict[int, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        ds = t.close_date or t.open_date
        ym = ds[:6]
        cm = int(ds[4:6])
        for bucket in (by_ym[ym], by_cm[cm]):
            bucket["trades"] += 1
            if t.realized_pnl() > 0:
                bucket["wins"] += 1
            bucket["pnl"] += t.realized_pnl()
    ym_out = {
        m: {
            "trades": d["trades"],
            "wins": d["wins"],
            "win_rate_pct": round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0,
            "pnl_krw": round(d["pnl"], 0),
        }
        for m, d in sorted(by_ym.items())
    }
    cm_out = {
        str(mn): {
            "trades": d["trades"],
            "wins": d["wins"],
            "win_rate_pct": round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0,
            "pnl_krw": round(d["pnl"], 0),
            "avg_pnl_per_trade_krw": round(d["pnl"] / d["trades"], 0) if d["trades"] else 0,
        }
        for mn, d in sorted(by_cm.items())
    }
    return {"by_year_month": ym_out, "by_calendar_month": cm_out}


def analyze_drawdown(pv_history: list[tuple[str, float]]) -> dict:
    if not pv_history:
        return {}
    dates = [d for d, _ in pv_history]
    pv = np.array([v for _, v in pv_history], dtype=float)
    cummax = np.maximum.accumulate(pv)
    dd = (pv - cummax) / cummax
    trough_idx = int(np.argmin(dd))
    mdd = float(dd[trough_idx])
    peak_pv = cummax[trough_idx]
    peak_idx = int(np.where(pv[:trough_idx + 1] == peak_pv)[0][0])
    recovery_idx = None
    for i in range(trough_idx, len(pv)):
        if pv[i] >= peak_pv:
            recovery_idx = i
            break
    dur_dd = int((datetime.strptime(dates[trough_idx], "%Y%m%d")
                  - datetime.strptime(dates[peak_idx], "%Y%m%d")).days)
    dur_rec = -1
    if recovery_idx is not None:
        dur_rec = int((datetime.strptime(dates[recovery_idx], "%Y%m%d")
                       - datetime.strptime(dates[trough_idx], "%Y%m%d")).days)
    return {
        "mdd_pct": round(mdd * 100, 2),
        "peak_date": dates[peak_idx],
        "peak_pv": round(float(peak_pv), 0),
        "trough_date": dates[trough_idx],
        "trough_pv": round(float(pv[trough_idx]), 0),
        "dd_duration_days": dur_dd,
        "recovery_date": dates[recovery_idx] if recovery_idx is not None else None,
        "recovery_duration_days": dur_rec,
        "recovered": recovery_idx is not None,
    }


def top_tickers_20(trades: list[bt.Position]) -> dict:
    per_ticker: dict[str, dict] = {}
    for t in trades:
        d = per_ticker.setdefault(
            t.ticker,
            {"ticker": t.ticker, "name": t.name, "trades": 0, "wins": 0,
             "total_pnl": 0.0, "total_cost": 0.0},
        )
        d["trades"] += 1
        d["total_pnl"] += t.realized_pnl()
        d["total_cost"] += t.total_cost
        if t.realized_pnl() > 0:
            d["wins"] += 1
    for d in per_ticker.values():
        d["win_rate_pct"] = round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0
        d["avg_return_pct"] = round(d["total_pnl"] / d["total_cost"] * 100, 2) if d["total_cost"] > 0 else 0
        d["total_pnl_krw"] = round(d["total_pnl"], 0)
        d["total_cost_krw"] = round(d["total_cost"], 0)
        del d["total_pnl"], d["total_cost"]
    ranked = sorted(per_ticker.values(), key=lambda x: -x["total_pnl_krw"])
    return {"winners": ranked[:20], "losers": ranked[-20:][::-1]}


def load_current_candidates_v2_dante_A() -> list[dict]:
    """2026-04-22 기준 현재 매수 후보 리스트 (v2_dante_A 조건)."""
    if not CANDIDATES_WITH_PRICES.exists():
        return []
    try:
        d = json.loads(CANDIDATES_WITH_PRICES.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for c in d.get("candidates", []):
        if c.get("score") != 4:
            continue
        if not c.get("entry_possible"):
            continue
        if c.get("warning"):
            continue
        if c.get("position_type") != "중장기":
            continue
        name = c.get("name") or ""
        if name.endswith("우") or name.endswith("우B"):
            continue
        out.append(c)
    return out


# ------------------------------ 리포트 ------------------------------
def write_report(payload: dict) -> str:
    L = []
    L.append("=" * 95)
    L.append("  v2_dante_A 최종 백테스트 리포트")
    L.append("  점수 4점 + KOSPI 200MA + 중장기만 + 우선주 제외")
    L.append(f"  기간: {payload['meta']['period']}  /  생성: {payload['meta']['generated_at']}")
    L.append("=" * 95)

    ov = payload["statistics"]["overall"]

    # [1] 전체 통계
    L.append("")
    L.append("[1] 전체 통계")
    L.append("-" * 95)
    L.append(f"  총 거래                  : {ov.get('total_trades', 0):,} 건")
    L.append(f"  승 / 패                  : {ov.get('win_count', 0):,} / {ov.get('loss_count', 0):,}")
    L.append(f"  승률                     : {ov.get('win_rate_pct', 0):.2f}%")
    L.append(f"  평균 거래 수익률         : {ov.get('avg_return_pct', 0):+.2f}%")
    L.append(f"  평균 승리 / 평균 손실    : {ov.get('avg_win_pct', 0):+.2f}% / {ov.get('avg_loss_pct', 0):+.2f}%")
    L.append(f"  최대 승리 / 최대 손실    : {ov.get('max_win_pct', 0):+.2f}% / {ov.get('max_loss_pct', 0):+.2f}%")
    L.append(f"  누적 수익률              : {ov.get('cumulative_return_pct', 0):+.2f}%")
    L.append(f"  CAGR (연환산)            : {ov.get('cagr_pct', 0):+.2f}%")
    L.append(f"  MDD (최대 낙폭)          : {ov.get('mdd_pct', 0):.2f}%")
    L.append(f"  Sharpe                   : {ov.get('sharpe', 0):.2f}")
    L.append(f"  초기 자본                : {ov.get('initial_capital', 0):,.0f}원")
    L.append(f"  최종 자산                : {ov.get('final_portfolio_value', 0):,.0f}원")
    L.append(f"  순수익                   : {ov.get('final_portfolio_value', 0) - ov.get('initial_capital', 0):,.0f}원")

    sm = payload["meta"].get("simulation_meta", {})
    if sm:
        L.append("")
        L.append(f"  [시뮬 메타] 고려 신호 {sm.get('n_signals_considered',0):,} / "
                 f"오픈 {sm.get('n_signals_opened',0):,} / "
                 f"약세장 스킵 {sm.get('n_bear_skipped',0):,}")

    # [2] 연도별
    L.append("")
    L.append("[2] 연도별 성과")
    L.append("-" * 95)
    L.append(f"  {'연도':<6} {'거래':>6} {'승률':>8} {'평균수익':>10} {'누적 P&L':>16}")
    for y, r in sorted(payload["statistics"]["by_year"].items()):
        L.append(f"  {y:<6} {r['trades']:>6} {r['win_rate_pct']:>7.2f}% "
                 f"{r['avg_return_pct']:>+9.2f}% {r['total_pnl_krw']:>16,.0f}")

    # [3] 매도 이유별
    L.append("")
    L.append("[3] 매도 이유별 분석 (주수 가중 1순위)")
    L.append("-" * 95)
    L.append(f"  {'사유':<12} {'건수':>6} {'비중':>7} {'승률':>8} {'평균':>9} {'중앙값':>9} {'최대':>9} {'최소':>9}")
    for r, d in payload["exit_reasons"].items():
        L.append(f"  {r:<12} {d['count']:>6} {d['share_pct']:>6.2f}% "
                 f"{d['win_rate_pct']:>7.2f}% {d['avg_return_pct']:>+8.2f}% "
                 f"{d['median_return_pct']:>+8.2f}% {d['max_return_pct']:>+8.2f}% "
                 f"{d['min_return_pct']:>+8.2f}%")

    # [4] TOP 20 Winners/Losers
    L.append("")
    L.append("[4] 종목별 TOP 20 Winners / Losers")
    L.append("-" * 95)
    L.append("  ── WINNERS ──")
    L.append(f"  {'#':<3} {'종목':<22} {'거래':>5} {'승률':>8} {'평균수익':>10} {'누적 P&L':>16}")
    for i, w in enumerate(payload["top_tickers_20"]["winners"], 1):
        L.append(f"  {i:<3} {w['ticker']} {w['name']:<17} {w['trades']:>5} "
                 f"{w['win_rate_pct']:>7.2f}% {w['avg_return_pct']:>+9.2f}% "
                 f"{w['total_pnl_krw']:>16,.0f}")
    L.append("")
    L.append("  ── LOSERS ──")
    L.append(f"  {'#':<3} {'종목':<22} {'거래':>5} {'승률':>8} {'평균수익':>10} {'누적 P&L':>16}")
    for i, w in enumerate(payload["top_tickers_20"]["losers"], 1):
        L.append(f"  {i:<3} {w['ticker']} {w['name']:<17} {w['trades']:>5} "
                 f"{w['win_rate_pct']:>7.2f}% {w['avg_return_pct']:>+9.2f}% "
                 f"{w['total_pnl_krw']:>16,.0f}")

    # [5] 월별 계절성
    L.append("")
    L.append("[5] 월별 계절성 (캘린더 월 1~12 통합)")
    L.append("-" * 95)
    L.append(f"  {'월':<4} {'거래':>6} {'승률':>8} {'총 P&L':>14} {'거래당 평균':>14}")
    for mn, r in payload["monthly"]["by_calendar_month"].items():
        L.append(f"  {mn:<4} {r['trades']:>6} {r['win_rate_pct']:>7.2f}% "
                 f"{r['pnl_krw']:>14,.0f} {r['avg_pnl_per_trade_krw']:>14,.0f}")

    L.append("")
    L.append("  ── YYYY-MM 별 (참고) ──")
    L.append(f"  {'월':<9} {'거래':>5} {'승률':>8} {'P&L':>14}")
    for m, r in payload["monthly"]["by_year_month"].items():
        L.append(f"  {m[:4]}-{m[4:6]:<3} {r['trades']:>5} "
                 f"{r['win_rate_pct']:>7.2f}% {r['pnl_krw']:>14,.0f}")

    # [6] MDD 상세
    d = payload["drawdown"]
    L.append("")
    L.append("[6] 최악의 구간 (MDD) 상세")
    L.append("-" * 95)
    L.append(f"  MDD              : {d.get('mdd_pct', 0)}%")
    L.append(f"  Peak 날짜/PV      : {d.get('peak_date')}  /  {d.get('peak_pv', 0):,.0f}원")
    L.append(f"  Trough 날짜/PV    : {d.get('trough_date')}  /  {d.get('trough_pv', 0):,.0f}원")
    L.append(f"  Peak→Trough      : {d.get('dd_duration_days')} 일")
    if d.get("recovered"):
        L.append(f"  Recovery 날짜     : {d.get('recovery_date')}")
        L.append(f"  Trough→Recovery  : {d.get('recovery_duration_days')} 일")
    else:
        L.append("  Recovery         : 미회복 (백테스트 종료 시점까지)")

    # [7] 현재 매수 후보
    cands = payload["current_candidates"]
    L.append("")
    L.append("[7] 현재 시점(2026-04-22) 매수 후보 — v2_dante_A 조건 충족")
    L.append("-" * 95)
    L.append(f"  조건: 점수=4 + entry_possible=True + warning 없음 + 중장기 + 우선주 제외")
    L.append(f"  총 후보: {len(cands)} 종목")
    if cands:
        L.append("")
        L.append(f"  {'#':<3} {'종목':<22} {'현재가':>10} {'추천목표':>10} {'목표%':>9} {'RR':>6} {'손절1':>10} {'손절1%':>8}")
        for i, c in enumerate(cands, 1):
            rt = c.get("recommended_target", {})
            sl1 = c.get("stop_loss", {}).get("1차", {})
            L.append(
                f"  {i:<3} {c['ticker']} {c.get('name', ''):<17} "
                f"{c.get('current_price', 0):>10,} "
                f"{rt.get('price', 0):>10,} {rt.get('percent', 0):>+8.2f}% "
                f"{(c.get('risk_reward_ratio') or 0):>6.2f} "
                f"{sl1.get('price', 0):>10,} {sl1.get('percent', 0):>+7.2f}%"
            )
    return "\n".join(L)


# ------------------------------ 메인 ------------------------------
def main() -> int:
    configure()
    bt.log.info("=" * 70)
    bt.log.info("v2_dante_A 최종 백테스트 + 상세 리포트")
    bt.log.info("=" * 70)

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)

    kospi_regime = bt.load_kospi_regime(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    trades, pv_history, sim_meta = bt.simulate(df, bt.CONFIG, kospi_regime)
    stats = bt.compute_statistics(trades, pv_history, bt.CONFIG)
    del df
    gc.collect()

    exit_reasons = analyze_exit_reasons(trades)
    monthly = analyze_monthly(trades)
    drawdown = analyze_drawdown(pv_history)
    top20 = top_tickers_20(trades)
    current_cands = load_current_candidates_v2_dante_A()

    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "period": f'{bt.CONFIG["start_date"]} ~ {bt.CONFIG["end_date"]}',
            "strategy": "v2_dante_A — 점수4 + KOSPI200MA + 중장기만 + 우선주 제외",
            "config_snapshot": {
                "initial_capital": bt.CONFIG["initial_capital"],
                "max_per_stock": bt.CONFIG["max_per_stock"],
                "stage_amounts": bt.CONFIG["stage_amounts"],
                "target_scores": bt.CONFIG["target_scores"],
                "allowed_position_types": bt.CONFIG["allowed_position_types"],
                "exclude_preferred_stocks": bt.CONFIG["exclude_preferred_stocks"],
                "enable_bear_market_filter": bt.CONFIG["enable_bear_market_filter"],
                "bear_market_ma_period": bt.CONFIG["bear_market_ma_period"],
                "stop_loss_1_base_deviation_pct": bt.CONFIG["stop_loss_1_base_deviation_pct"],
                "min_recommended_pct_exclusive": bt.CONFIG["min_recommended_pct_exclusive"],
                "min_daily_trading_value": bt.CONFIG["min_daily_trading_value"],
                "buy_commission_rate": bt.CONFIG["buy_commission_rate"],
                "sell_commission_rate": bt.CONFIG["sell_commission_rate"],
                "sell_tax_rate": bt.CONFIG["sell_tax_rate"],
                "slippage_rate": bt.CONFIG["slippage_rate"],
                "max_hold_days": bt.CONFIG["max_hold_days"],
            },
            "simulation_meta": sim_meta,
            "limitations": [
                "현재 상장 종목만 대상 (생존 편향)",
                "동일 종목 중복 보유 금지 (현재 열린 포지션 한정)",
                "호가 단위 스냅 체결 가정",
                "KOSPI proxy: DB 기반 KOSPI 시가총액 합계",
            ],
        },
        "statistics": stats,
        "exit_reasons": exit_reasons,
        "monthly": monthly,
        "drawdown": drawdown,
        "top_tickers_20": top20,
        "current_candidates": current_cands,
    }

    bt.save_json_atomic(payload, OUT_JSON)
    report = write_report(payload)
    OUT_TXT.write_text(report, encoding="utf-8")

    n = stats["overall"].get("total_trades", 0)
    cum = stats["overall"].get("cumulative_return_pct", 0)
    bt.log.info(f"저장: {OUT_JSON.name} + {OUT_TXT.name} / {time.time()-t0:.1f}s / "
                f"거래 {n:,}건 / 누적 {cum:+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
