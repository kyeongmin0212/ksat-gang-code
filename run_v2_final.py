"""v2 전략 최종 백테스트 + 상세 리포트.

설정 (확정)
- 점수 4점만 매매
- KOSPI 200MA 약세장 필터
- 분할매수 1:2:4:8
- 이중 손절 (1차 -1.5% 반매도 / 2차 구름하단 전량)
- 목표가 3가지 중간값
- 자본 10M / 종목당 1M / 수수료·세금 동일

출력
- backtest_results_v2_final.json
- backtest_v2_final_report.txt
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
OUT_JSON = BASE / "backtest_results_v2_final.json"
OUT_TXT = BASE / "backtest_v2_final_report.txt"
CANDIDATES_WITH_PRICES = BASE / "candidates_with_prices.json"


def configure_v2() -> None:
    """모든 v5/v6 플래그 리셋 → 순수 v2 동작."""
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"

    # v2 핵심
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False

    # v5 플래그 OFF
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False
    bt.CONFIG["enable_trailing_stop"] = False

    # v6 플래그 OFF
    bt.CONFIG["target_strategy"] = "median"
    bt.CONFIG["simple_stop_loss_pct"] = None

    # 비용 (spec 재확인)
    bt.CONFIG["buy_commission_rate"] = 0.00015
    bt.CONFIG["sell_commission_rate"] = 0.00015
    bt.CONFIG["sell_tax_rate"] = 0.0023
    bt.CONFIG["slippage_rate"] = 0.001


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
        r = share_weighted_reason(t)
        by_reason[r].append(t.realized_pnl_pct())
    total = len(trades) or 1
    out: dict[str, dict] = {}
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


def load_current_v2_candidates() -> list[dict]:
    """candidates_with_prices.json → v2 조건(score=4, entry_possible=True, warning=None)."""
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
        out.append(c)
    return out


def write_report(payload: dict) -> str:
    L = []
    L.append("=" * 90)
    L.append("  v2 전략 최종 백테스트 리포트")
    L.append("  점수 4점 + KOSPI 200MA 약세장 필터 + 이중 손절 + 중간값 목표")
    L.append(f"  기간: {payload['meta']['period']}")
    L.append(f"  생성 시각: {payload['meta']['generated_at']}")
    L.append("=" * 90)

    # [1] 전체 통계
    ov = payload["statistics"]["overall"]
    L.append("")
    L.append("[1] 전체 통계")
    L.append("-" * 90)
    L.append(f"  총 거래                     : {ov.get('total_trades', 0):,} 건")
    L.append(f"  승 / 패                     : {ov.get('win_count', 0):,} / {ov.get('loss_count', 0):,}")
    L.append(f"  승률                        : {ov.get('win_rate_pct', 0):.2f}%")
    L.append(f"  평균 거래 수익률            : {ov.get('avg_return_pct', 0):+.2f}%")
    L.append(f"  평균 승리 / 평균 손실       : {ov.get('avg_win_pct', 0):+.2f}% / {ov.get('avg_loss_pct', 0):+.2f}%")
    L.append(f"  최대 승리 / 최대 손실       : {ov.get('max_win_pct', 0):+.2f}% / {ov.get('max_loss_pct', 0):+.2f}%")
    L.append(f"  누적 수익률                 : {ov.get('cumulative_return_pct', 0):+.2f}%")
    L.append(f"  CAGR (연환산)               : {ov.get('cagr_pct', 0):+.2f}%")
    L.append(f"  MDD (최대 낙폭)             : {ov.get('mdd_pct', 0):.2f}%")
    L.append(f"  Sharpe                      : {ov.get('sharpe', 0):.2f}")
    L.append(f"  초기 자본                   : {ov.get('initial_capital', 0):,.0f}원")
    L.append(f"  최종 자산                   : {ov.get('final_portfolio_value', 0):,.0f}원")
    L.append(f"  순수익                      : {ov.get('final_portfolio_value', 0) - ov.get('initial_capital', 0):,.0f}원")

    sm = payload["meta"].get("simulation_meta", {})
    if sm:
        L.append("")
        L.append("  [시뮬 메타]")
        L.append(f"    고려된 신호 (bull 일자): {sm.get('n_signals_considered', 0):,}")
        L.append(f"    실제 오픈된 포지션     : {sm.get('n_signals_opened', 0):,}")
        L.append(f"    약세장 스킵 신호       : {sm.get('n_bear_skipped', 0):,}")

    # [2] 연도별
    L.append("")
    L.append("[2] 연도별 성과")
    L.append("-" * 90)
    L.append(f"  {'연도':<6} {'거래':>6} {'승률':>8} {'평균수익':>10} {'누적 P&L':>16}")
    for y, r in sorted(payload["statistics"]["by_year"].items()):
        L.append(
            f"  {y:<6} {r['trades']:>6} {r['win_rate_pct']:>7.2f}% "
            f"{r['avg_return_pct']:>+9.2f}% {r['total_pnl_krw']:>16,.0f}"
        )

    # [3] 포지션 타입별
    L.append("")
    L.append("[3] 포지션 타입별 성과")
    L.append("-" * 90)
    L.append(f"  {'타입':<14} {'거래':>6} {'승률':>8} {'평균수익':>10} {'평균보유':>8} {'누적 P&L':>16}")
    for pt, r in payload["statistics"]["by_position_type"].items():
        L.append(
            f"  {pt:<14} {r['trades']:>6} {r['win_rate_pct']:>7.2f}% "
            f"{r['avg_return_pct']:>+9.2f}% {r['avg_hold_days']:>7.1f}일 "
            f"{r['total_pnl_krw']:>16,.0f}"
        )

    # [4] 매도 이유별
    L.append("")
    L.append("[4] 매도 이유별 분석 (주수 가중 1순위 기준)")
    L.append("-" * 90)
    L.append(
        f"  {'사유':<12} {'건수':>6} {'비중':>7} {'승률':>8} {'평균':>9} {'중앙값':>9} {'최대':>9} {'최소':>9}"
    )
    for r, d in payload["exit_reasons"].items():
        L.append(
            f"  {r:<12} {d['count']:>6} {d['share_pct']:>6.2f}% {d['win_rate_pct']:>7.2f}% "
            f"{d['avg_return_pct']:>+8.2f}% {d['median_return_pct']:>+8.2f}% "
            f"{d['max_return_pct']:>+8.2f}% {d['min_return_pct']:>+8.2f}%"
        )

    # [5] TOP 10 Winners / Losers
    L.append("")
    L.append("[5] 종목별 TOP 10 Winners / Losers (누적 P&L 기준)")
    L.append("-" * 90)
    L.append("  ── WINNERS ──")
    L.append(f"  {'#':<3} {'종목':<22} {'거래':>5} {'승률':>8} {'평균수익':>10} {'누적 P&L':>16}")
    for i, w in enumerate(payload["statistics"]["top10_winners_by_ticker"], 1):
        L.append(
            f"  {i:<3} {w['ticker']} {w['name']:<17} {w['trades']:>5} "
            f"{w.get('win_rate_pct', 0):>7.2f}% {w.get('avg_return_pct', 0):>+9.2f}% "
            f"{w.get('total_pnl_krw', 0):>16,.0f}"
        )
    L.append("")
    L.append("  ── LOSERS ──")
    L.append(f"  {'#':<3} {'종목':<22} {'거래':>5} {'승률':>8} {'평균수익':>10} {'누적 P&L':>16}")
    for i, w in enumerate(payload["statistics"]["top10_losers_by_ticker"], 1):
        L.append(
            f"  {i:<3} {w['ticker']} {w['name']:<17} {w['trades']:>5} "
            f"{w.get('win_rate_pct', 0):>7.2f}% {w.get('avg_return_pct', 0):>+9.2f}% "
            f"{w.get('total_pnl_krw', 0):>16,.0f}"
        )

    # [6] 현재 매수 후보
    L.append("")
    L.append("[6] 현재 시점 매수 후보 (2026-04-22, v2 조건 충족)")
    L.append("-" * 90)
    cands = payload["current_candidates"]
    L.append(f"  조건: 점수=4 + entry_possible=True + warning 없음")
    L.append(f"  총 후보: {len(cands)} 종목")
    if cands:
        L.append("")
        L.append(
            f"  {'#':<3} {'종목':<22} {'포지션':<10} {'현재가':>10} "
            f"{'추천목표':>10} {'목표%':>8} {'RR':>6}"
        )
        for i, c in enumerate(cands[:40], 1):
            rt = c.get("recommended_target", {})
            L.append(
                f"  {i:<3} {c['ticker']} {c.get('name', ''):<17} "
                f"{c.get('position_type', ''):<10} {c.get('current_price', 0):>10,} "
                f"{rt.get('price', 0):>10,} {rt.get('percent', 0):>+7.2f}% "
                f"{(c.get('risk_reward_ratio') or 0):>6.2f}"
            )
        if len(cands) > 40:
            L.append(f"  ... 외 {len(cands) - 40} 종목")

    return "\n".join(L)


def main() -> int:
    configure_v2()
    bt.log.info("=" * 70)
    bt.log.info("v2 최종 백테스트 시작")
    bt.log.info("=" * 70)

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"])
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)

    kospi_regime = bt.load_kospi_regime(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    trades, pv_history, sim_meta = bt.simulate(df, bt.CONFIG, kospi_regime)

    stats = bt.compute_statistics(trades, pv_history, bt.CONFIG)
    del df
    gc.collect()

    # 매도 이유별 분석
    exit_reasons = analyze_exit_reasons(trades)

    # 종목별 win_rate 보강 (compute_statistics는 미계산)
    ticker_wins: dict[str, dict] = defaultdict(lambda: {"wins": 0, "trades": 0})
    for t in trades:
        ticker_wins[t.ticker]["trades"] += 1
        if t.realized_pnl() > 0:
            ticker_wins[t.ticker]["wins"] += 1
    for side in ("top10_winners_by_ticker", "top10_losers_by_ticker"):
        for e in stats[side]:
            tw = ticker_wins.get(e["ticker"], {"wins": 0, "trades": 1})
            e["win_rate_pct"] = round(100 * tw["wins"] / tw["trades"], 2) if tw["trades"] else 0

    # 현재 후보 (candidates_with_prices.json 에서 v2 조건 필터)
    current_cands = load_current_v2_candidates()

    # TOP 20으로 확장된 winners/losers (기존 TOP 10 구조 유지)
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "period": f'{bt.CONFIG["start_date"]} ~ {bt.CONFIG["end_date"]}',
            "strategy": "v2_final (점수4 + KOSPI 200MA + 2단 손절 + 중간값 목표)",
            "config_snapshot": {
                "initial_capital": bt.CONFIG["initial_capital"],
                "max_per_stock": bt.CONFIG["max_per_stock"],
                "stage_amounts": bt.CONFIG["stage_amounts"],
                "target_scores": bt.CONFIG["target_scores"],
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
                "현재 상장 종목만 대상 (생존 편향 있음)",
                "동일 종목 중복 보유 금지 (현재 오픈 중일 때만 중복 차단)",
                "호가 단위 스냅 후 체결 가정",
                "KOSPI proxy: KRX 로그인 부재 → DB 기반 KOSPI 시총 합계 사용",
            ],
        },
        "statistics": stats,
        "exit_reasons": exit_reasons,
        "current_candidates": current_cands,
    }

    bt.save_json_atomic(payload, OUT_JSON)
    report = write_report(payload)
    OUT_TXT.write_text(report, encoding="utf-8")

    n_trades = stats['overall'].get('total_trades', 0)
    cum = stats['overall'].get('cumulative_return_pct', 0)
    bt.log.info(
        f"저장: {OUT_JSON.name} + {OUT_TXT.name} / 총 {time.time()-t0:.1f}s / "
        f"거래 {n_trades:,}건 / 누적 {cum:+.2f}%"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
