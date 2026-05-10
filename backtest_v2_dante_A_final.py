"""v2_dante_A 최종 확정 백테스트 + 상세 리포트 (신규 스크립트).

제약 준수
- 기존 .py/.json/.db 파일 수정 없음
- backtesting 모듈은 read-only import 후 CONFIG(dict) 메모리 덮어쓰기만
- 실제 매매 API 호출 없음

로컬 재구현 항목 (기존 파일 수정 대신)
- 우C 필터: backtesting.py SQL 은 우/우B 만 제외하므로 DataFrame 에서 추가 제거
- 분할매수 비율: 스펙 '1:2:4:8 (15단위), 100만원' → [66666, 133333, 266666, 533334]
  (기존 backtesting.CONFIG 기본값 [100k/200k/400k/300k] 와 다름 — 메모리에서 덮어씀)
- MDD TOP 3: backtesting.py 의 단일 MDD 분석을 확장하여 상위 3개 drawdown 에피소드 발굴
- 매도 이유별 분석, 월별 계절성, 현재 후보 필터: 로컬 구현

출력
- backtest_results_v2_dante_A_final.json
- backtest_report_v2_dante_A_final.txt
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
import backtesting as bt  # noqa — read-only

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT_JSON = BASE / "backtest_results_v2_dante_A_final.json"
OUT_TXT = BASE / "backtest_report_v2_dante_A_final.txt"
CANDIDATES_WITH_PRICES = BASE / "candidates_with_prices.json"


# ============================================================
# 1. 전략 config (메모리 덮어쓰기 — 파일 변경 없음)
# ============================================================
def configure() -> None:
    # 기간/자본
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"
    bt.CONFIG["initial_capital"] = 10_000_000
    bt.CONFIG["max_per_stock"] = 1_000_000

    # 분할매수 비율 1:2:4:8 (총 15단위) × 100만원 cap
    bud = bt.CONFIG["max_per_stock"]
    bt.CONFIG["stage_amounts"] = [
        bud * 1 // 15,        # 66,666
        bud * 2 // 15,        # 133,333
        bud * 4 // 15,        # 266,666
        bud - (bud*1//15 + bud*2//15 + bud*4//15),  # 나머지 ≈ 533,334
    ]

    # 필터
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False
    bt.CONFIG["exclude_preferred_stocks"] = True            # SQL: 우, 우B 제외 (우C 는 로컬 추가)
    bt.CONFIG["allowed_position_types"] = ["중장기"]

    # v5/v6 플래그 OFF (v2 순수)
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False
    bt.CONFIG["enable_trailing_stop"] = False
    bt.CONFIG["target_strategy"] = "median"
    bt.CONFIG["simple_stop_loss_pct"] = None

    # 비용
    bt.CONFIG["buy_commission_rate"] = 0.00015
    bt.CONFIG["sell_commission_rate"] = 0.00015
    bt.CONFIG["sell_tax_rate"] = 0.0023
    bt.CONFIG["slippage_rate"] = 0.001


# ============================================================
# 2. 로컬 필터 — 우C
# ============================================================
def apply_local_우C_filter(df):
    """backtesting SQL은 우/우B만 제외 → 우C 끝 종목 여기서 추가 제거."""
    mask = ~df["종목명"].astype(str).str.endswith("우C")
    removed = int((~mask).sum())
    if removed:
        bt.log.info(f"[로컬] '우C' 끝 종목 {removed} 행 추가 제거")
    return df[mask].reset_index(drop=True)


def looks_preferred(name: str) -> bool:
    if not isinstance(name, str):
        return False
    return name.endswith("우") or name.endswith("우B") or name.endswith("우C")


# ============================================================
# 3. 로컬 분석 함수
# ============================================================
def share_weighted_reason(pos: bt.Position) -> str:
    if not pos.sell_events:
        return "미체결"
    bucket: dict[str, int] = {}
    for ev in pos.sell_events:
        bucket[ev["reason"]] = bucket.get(ev["reason"], 0) + ev["shares"]
    return max(bucket.items(), key=lambda x: x[1])[0]


def days_between(a: str, b: str) -> int:
    """영업일 아닌 달력일 기준 보유일."""
    da = datetime.strptime(a, "%Y%m%d")
    db = datetime.strptime(b, "%Y%m%d")
    return max(0, (db - da).days)


def analyze_exit_reasons(trades: list[bt.Position]) -> dict:
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        r = share_weighted_reason(t)
        by_reason[r].append({
            "return_pct": t.realized_pnl_pct(),
            "pnl_krw": t.realized_pnl(),
        })
    total = len(trades) or 1
    out = {}
    for r, items in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        rets = [it["return_pct"] for it in items]
        pnls = [it["pnl_krw"] for it in items]
        wins = [x for x in rets if x > 0]
        out[r] = {
            "count": len(rets),
            "share_pct": round(100 * len(rets) / total, 2),
            "win_rate_pct": round(100 * len(wins) / len(rets), 2) if rets else 0,
            "avg_return_pct": round(float(np.mean(rets)), 2),
            "median_return_pct": round(float(np.median(rets)), 2),
            "total_pnl_krw": round(sum(pnls), 0),
            "max_return_pct": round(max(rets), 2),
            "min_return_pct": round(min(rets), 2),
        }
    return out


def analyze_monthly(trades: list[bt.Position]) -> dict:
    """월 통합(1~12) + 연×월 히트맵 데이터."""
    by_month_all: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "rets": []})
    by_ym: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        ds = t.close_date or t.open_date
        mn = int(ds[4:6])
        ym = ds[:6]
        for d in (by_month_all[str(mn)], by_ym[ym]):
            d["trades"] += 1
            if t.realized_pnl() > 0:
                d["wins"] += 1
            d["pnl"] += t.realized_pnl()
        by_month_all[str(mn)]["rets"].append(t.realized_pnl_pct())

    out_m = {}
    for mn, d in sorted(by_month_all.items(), key=lambda x: int(x[0])):
        out_m[mn] = {
            "trades": d["trades"],
            "win_rate_pct": round(d["wins"]/d["trades"]*100, 2) if d["trades"] else 0,
            "avg_return_pct": round(float(np.mean(d["rets"])), 2) if d["rets"] else 0,
            "total_pnl_krw": round(d["pnl"], 0),
        }
    return {"by_calendar_month": out_m, "by_year_month": {m: dict(v) for m, v in sorted(by_ym.items())}}


def top_tickers_20(trades: list[bt.Position]) -> dict:
    per: dict[str, dict] = {}
    for t in trades:
        d = per.setdefault(t.ticker, {
            "ticker": t.ticker, "name": t.name, "trades": 0, "wins": 0,
            "total_pnl": 0.0, "total_cost": 0.0, "total_hold_days": 0,
        })
        d["trades"] += 1
        d["total_pnl"] += t.realized_pnl()
        d["total_cost"] += t.total_cost
        if t.realized_pnl() > 0:
            d["wins"] += 1
        if t.close_date:
            d["total_hold_days"] += days_between(t.open_date, t.close_date)
    for d in per.values():
        d["win_rate_pct"] = round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0
        d["avg_return_pct"] = (
            round(d["total_pnl"] / d["total_cost"] * 100, 2) if d["total_cost"] > 0 else 0
        )
        d["avg_hold_days"] = round(d["total_hold_days"] / d["trades"], 1) if d["trades"] else 0
        d["total_pnl_krw"] = round(d["total_pnl"], 0)
        d["total_cost_krw"] = round(d["total_cost"], 0)
        del d["total_pnl"], d["total_cost"], d["total_hold_days"]
    ranked = sorted(per.values(), key=lambda x: -x["total_pnl_krw"])
    return {"winners": ranked[:20], "losers": ranked[-20:][::-1]}


# ---------- MDD TOP 3 ----------
def find_drawdown_episodes(pv_history: list[tuple[str, float]]) -> list[dict]:
    """모든 drawdown 에피소드 (peak → trough → recovery) 반환."""
    if not pv_history:
        return []
    dates = [d for d, _ in pv_history]
    pv = np.array([v for _, v in pv_history], dtype=float)

    episodes = []
    current_peak = pv[0]
    peak_idx = 0
    trough_pv = pv[0]
    trough_idx = 0
    in_dd = False

    for i in range(len(pv)):
        if pv[i] >= current_peak:
            if in_dd:
                episodes.append({
                    "peak_idx": peak_idx, "trough_idx": trough_idx, "recovery_idx": i,
                })
                in_dd = False
            current_peak = pv[i]
            peak_idx = i
            trough_pv = pv[i]
            trough_idx = i
        else:
            if pv[i] < trough_pv:
                trough_pv = pv[i]
                trough_idx = i
            in_dd = True
    if in_dd:
        episodes.append({
            "peak_idx": peak_idx, "trough_idx": trough_idx, "recovery_idx": None,
        })

    # 크기 계산 + 상세 attach
    for ep in episodes:
        p = pv[ep["peak_idx"]]
        t = pv[ep["trough_idx"]]
        ep["peak_date"] = dates[ep["peak_idx"]]
        ep["trough_date"] = dates[ep["trough_idx"]]
        ep["peak_pv"] = float(p)
        ep["trough_pv"] = float(t)
        ep["mdd_pct"] = round((t - p) / p * 100, 2) if p > 0 else 0
        ep["dd_days"] = (datetime.strptime(dates[ep["trough_idx"]], "%Y%m%d")
                         - datetime.strptime(dates[ep["peak_idx"]], "%Y%m%d")).days
        if ep["recovery_idx"] is not None:
            ep["recovery_date"] = dates[ep["recovery_idx"]]
            ep["recovery_days"] = (datetime.strptime(dates[ep["recovery_idx"]], "%Y%m%d")
                                   - datetime.strptime(dates[ep["trough_idx"]], "%Y%m%d")).days
            ep["recovered"] = True
        else:
            ep["recovery_date"] = None
            ep["recovery_days"] = None
            ep["recovered"] = False
    return episodes


def mdd_top3_with_losers(
    pv_history: list[tuple[str, float]], trades: list[bt.Position], n_losers: int = 5
) -> list[dict]:
    eps = find_drawdown_episodes(pv_history)
    eps.sort(key=lambda e: e["mdd_pct"])  # 가장 음수 먼저
    top3 = eps[:3]
    for ep in top3:
        s = ep["peak_date"]
        e = ep["trough_date"]
        ep_trades = [
            t for t in trades
            if t.close_date and s <= t.close_date <= e and t.realized_pnl() < 0
        ]
        ep_trades.sort(key=lambda t: t.realized_pnl())
        ep["major_losers"] = [
            {
                "ticker": t.ticker, "name": t.name,
                "close_date": t.close_date,
                "pnl_krw": round(t.realized_pnl(), 0),
                "return_pct": round(t.realized_pnl_pct(), 2),
            }
            for t in ep_trades[:n_losers]
        ]
    return top3


# ---------- 현재 후보 ----------
def load_current_candidates() -> list[dict]:
    """candidates_with_prices.json 에서 v2_dante_A 조건 충족만 선택."""
    if not CANDIDATES_WITH_PRICES.exists():
        return []
    try:
        data = json.loads(CANDIDATES_WITH_PRICES.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for c in data.get("candidates", []):
        if c.get("score") != 4:
            continue
        if not c.get("entry_possible"):
            continue
        if c.get("warning"):
            continue
        if c.get("position_type") != "중장기":
            continue
        if looks_preferred(c.get("name") or ""):
            continue
        out.append(c)
    out.sort(key=lambda c: -(c.get("recommended_target", {}).get("percent") or 0))
    return out


# ============================================================
# 4. 리포트 렌더링
# ============================================================
def render_report(payload: dict) -> str:
    L = []
    L.append("=" * 100)
    L.append("  v2_dante_A 최종 확정 백테스트 리포트")
    L.append("  조건: 점수 4점 + KOSPI 200MA + 중장기만 + 우선주 제외(우/우B/우C) + 이중 손절")
    L.append(f"  분할매수 stage_amounts = {bt.CONFIG['stage_amounts']} (1:2:4:8 / 100만원 내)")
    L.append(f"  기간: {payload['meta']['period']}  /  생성: {payload['meta']['generated_at']}")
    L.append("=" * 100)

    # [1] 전체 통계
    ov = payload["statistics"]["overall"]
    # 보유일/손익비 보강
    extra = payload["extras"]
    L.append("")
    L.append("[1] 전체 통계")
    L.append("-" * 100)
    L.append(f"  총 거래                  : {ov.get('total_trades', 0):,} 건  (승 {ov.get('win_count', 0):,} / 패 {ov.get('loss_count', 0):,})")
    L.append(f"  승률                     : {ov.get('win_rate_pct', 0):.2f}%")
    L.append(f"  평균 거래 수익률         : {ov.get('avg_return_pct', 0):+.2f}%")
    L.append(f"  평균 익절 수익률         : {ov.get('avg_win_pct', 0):+.2f}%   (평균 손절 손실률: {ov.get('avg_loss_pct', 0):+.2f}%)")
    L.append(f"  손익비 (Payoff)          : {extra['payoff_ratio']}")
    L.append(f"  Profit Factor            : {extra['profit_factor']}")
    L.append(f"  평균 보유일 (달력일)     : {extra['avg_hold_days']}")
    L.append(f"  최대 승리 / 최대 손실    : {ov.get('max_win_pct', 0):+.2f}% / {ov.get('max_loss_pct', 0):+.2f}%")
    L.append(f"  누적 수익률              : {ov.get('cumulative_return_pct', 0):+.2f}%")
    L.append(f"  CAGR                     : {ov.get('cagr_pct', 0):+.2f}%")
    L.append(f"  MDD (최대)               : {ov.get('mdd_pct', 0):.2f}%")
    L.append(f"  Sharpe                   : {ov.get('sharpe', 0):.2f}")
    L.append(f"  초기 → 최종              : {ov.get('initial_capital', 0):,.0f}원 → {ov.get('final_portfolio_value', 0):,.0f}원")
    L.append(f"  순수익                   : {ov.get('final_portfolio_value', 0) - ov.get('initial_capital', 0):+,.0f}원")
    sm = payload["meta"].get("simulation_meta", {})
    if sm:
        L.append(f"  [시뮬 메타] 고려 신호 {sm.get('n_signals_considered',0):,} / "
                 f"오픈 {sm.get('n_signals_opened',0):,} / 약세장 스킵 {sm.get('n_bear_skipped',0):,}")

    # [2] 연도별
    L.append("")
    L.append("[2] 연도별 성과")
    L.append("-" * 100)
    L.append(f"  {'연도':<6} {'거래':>6} {'승률':>8} {'평균수익':>10} {'연 P&L':>16} {'누적 수익률':>14}")
    cum = 0.0
    initial = ov.get("initial_capital", 10_000_000)
    sorted_years = sorted(payload["statistics"]["by_year"].items())
    for y, r in sorted_years:
        cum += r["total_pnl_krw"]
        cum_pct = cum / initial * 100
        L.append(f"  {y:<6} {r['trades']:>6} {r['win_rate_pct']:>7.2f}% "
                 f"{r['avg_return_pct']:>+9.2f}% {r['total_pnl_krw']:>16,.0f} {cum_pct:>+13.2f}%")

    # [3] 매도 이유별
    L.append("")
    L.append("[3] 매도 이유별 분석 (주수 가중 1순위)")
    L.append("-" * 100)
    L.append(f"  {'사유':<12} {'건수':>6} {'비중':>7} {'승률':>8} {'평균수익':>10} {'중앙값':>10} {'총 P&L':>16}")
    for r, d in payload["exit_reasons"].items():
        L.append(
            f"  {r:<12} {d['count']:>6} {d['share_pct']:>6.2f}% {d['win_rate_pct']:>7.2f}% "
            f"{d['avg_return_pct']:>+9.2f}% {d['median_return_pct']:>+9.2f}% {d['total_pnl_krw']:>16,.0f}"
        )

    # [4] TOP 20
    L.append("")
    L.append("[4] 종목별 TOP 20")
    L.append("-" * 100)
    L.append("  ── WINNERS (누적 P&L 기준 상위) ──")
    L.append(f"  {'#':<3} {'종목':<24} {'거래':>5} {'승률':>8} {'평균수익':>10} {'평균보유(일)':>12} {'누적 P&L':>16}")
    for i, w in enumerate(payload["top_tickers_20"]["winners"], 1):
        L.append(f"  {i:<3} {w['ticker']} {w['name']:<19} {w['trades']:>5} "
                 f"{w['win_rate_pct']:>7.2f}% {w['avg_return_pct']:>+9.2f}% "
                 f"{w['avg_hold_days']:>11.1f} {w['total_pnl_krw']:>16,.0f}")
    L.append("")
    L.append("  ── LOSERS ──")
    L.append(f"  {'#':<3} {'종목':<24} {'거래':>5} {'승률':>8} {'평균수익':>10} {'평균보유(일)':>12} {'누적 P&L':>16}")
    for i, w in enumerate(payload["top_tickers_20"]["losers"], 1):
        L.append(f"  {i:<3} {w['ticker']} {w['name']:<19} {w['trades']:>5} "
                 f"{w['win_rate_pct']:>7.2f}% {w['avg_return_pct']:>+9.2f}% "
                 f"{w['avg_hold_days']:>11.1f} {w['total_pnl_krw']:>16,.0f}")

    # [5] 월별 계절성
    L.append("")
    L.append("[5] 월별 계절성")
    L.append("-" * 100)
    L.append("  ── 캘린더 월 (1~12 통합) ──")
    L.append(f"  {'월':<4} {'거래':>6} {'승률':>8} {'평균수익':>10} {'총 P&L':>16}")
    cm = payload["monthly"]["by_calendar_month"]
    for mn in range(1, 13):
        r = cm.get(str(mn))
        if not r:
            L.append(f"  {mn:<4} {'-':>6} {'-':>8} {'-':>10} {'-':>16}")
        else:
            L.append(f"  {mn:<4} {r['trades']:>6} {r['win_rate_pct']:>7.2f}% "
                     f"{r['avg_return_pct']:>+9.2f}% {r['total_pnl_krw']:>16,.0f}")

    # 연×월 히트맵
    L.append("")
    L.append("  ── 연도 × 월 히트맵 (총 P&L, 원) ──")
    ym = payload["monthly"]["by_year_month"]
    years = sorted({k[:4] for k in ym.keys()})
    header = f"  {'연도':<6}" + "".join(f"{mo:>11}" for mo in range(1, 13))
    L.append(header)
    L.append("  " + "-" * (len(header) - 2))
    for y in years:
        cells = []
        for mo in range(1, 13):
            k = f"{y}{mo:02d}"
            r = ym.get(k)
            if not r:
                cells.append(f"{'·':>11}")
            else:
                pnl = r.get("pnl_krw") if "pnl_krw" in r else r.get("pnl", 0)
                cells.append(f"{pnl:>+11,.0f}")
        L.append(f"  {y:<6}" + "".join(cells))

    # [6] MDD TOP 3
    L.append("")
    L.append("[6] 최악의 MDD 구간 TOP 3")
    L.append("-" * 100)
    for i, ep in enumerate(payload["mdd_top3"], 1):
        L.append("")
        L.append(f"  ── #{i} — MDD {ep['mdd_pct']:.2f}% ──")
        L.append(f"    Peak     : {ep['peak_date']}  PV {ep['peak_pv']:,.0f}원")
        L.append(f"    Trough   : {ep['trough_date']}  PV {ep['trough_pv']:,.0f}원 "
                 f"(Peak→Trough {ep['dd_days']}일)")
        if ep["recovered"]:
            L.append(f"    Recovery : {ep['recovery_date']}  (Trough→Recovery {ep['recovery_days']}일)")
        else:
            L.append(f"    Recovery : 미회복 (백테스트 종료까지)")
        if ep.get("major_losers"):
            L.append(f"    주요 손실 종목 (DD 구간 중 청산, 상위 5):")
            for lo in ep["major_losers"]:
                L.append(f"      · {lo['ticker']} {lo['name']:<18} {lo['close_date']}  "
                         f"{lo['return_pct']:>+7.2f}%  {lo['pnl_krw']:>+12,.0f}원")

    # [7] 현재 후보
    L.append("")
    L.append("[7] 현재 시점 (2026-04-22) 매수 후보 — v2_dante_A 조건 전체")
    L.append("-" * 100)
    cands = payload["current_candidates"]
    L.append(f"  필터: 점수=4 / entry_possible=True / warning 없음 / 중장기 / 우/우B/우C 제외")
    L.append(f"  총 후보: {len(cands)} 종목")
    if cands:
        L.append("")
        L.append(f"  {'#':<3} {'종목':<24} {'현재가':>11} {'1차매수':>10} {'목표':>12} "
                 f"{'목표%':>8} {'RR':>7} {'1차손절':>10} {'손절%':>8}")
        for i, c in enumerate(cands, 1):
            rt = c.get("recommended_target", {})
            buy1 = c.get("buy_prices", {}).get("1차", {})
            sl1 = c.get("stop_loss", {}).get("1차", {})
            L.append(
                f"  {i:<3} {c['ticker']} {c.get('name', ''):<19} "
                f"{c.get('current_price', 0):>11,} {buy1.get('price', 0):>10,} "
                f"{rt.get('price', 0):>12,} {rt.get('percent', 0):>+7.2f}% "
                f"{(c.get('risk_reward_ratio') or 0):>7.2f} "
                f"{sl1.get('price', 0):>10,} {sl1.get('percent', 0):>+7.2f}%"
            )

    return "\n".join(L)


# ============================================================
# 5. 메인
# ============================================================
def main() -> int:
    configure()
    bt.log.info("=" * 70)
    bt.log.info("v2_dante_A 최종 확정 백테스트 (기존 파일 수정 없이)")
    bt.log.info(f"stage_amounts={bt.CONFIG['stage_amounts']}")
    bt.log.info("=" * 70)

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    df = apply_local_우C_filter(df)  # 우C 추가 필터
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)

    kospi_regime = bt.load_kospi_regime(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    trades, pv_history, sim_meta = bt.simulate(df, bt.CONFIG, kospi_regime)

    stats = bt.compute_statistics(trades, pv_history, bt.CONFIG)
    del df
    gc.collect()

    # 보유일/손익비 보강
    holds = [days_between(t.open_date, t.close_date or t.open_date) for t in trades if t.close_date]
    rets = [t.realized_pnl_pct() for t in trades]
    pnls = [t.realized_pnl() for t in trades]
    wins_r = [r for r in rets if r > 0]
    losses_r = [r for r in rets if r <= 0]
    avg_win = float(np.mean(wins_r)) if wins_r else 0
    avg_loss = float(np.mean(losses_r)) if losses_r else 0
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    extras = {
        "avg_hold_days": round(float(np.mean(holds)), 2) if holds else 0,
        "payoff_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
    }

    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "period": f'{bt.CONFIG["start_date"]} ~ {bt.CONFIG["end_date"]}',
            "strategy": "v2_dante_A — 점수4 + KOSPI 200MA + 중장기만 + 우선주(우/우B/우C) 제외",
            "stage_amounts": bt.CONFIG["stage_amounts"],
            "config_snapshot": {
                "initial_capital": bt.CONFIG["initial_capital"],
                "max_per_stock": bt.CONFIG["max_per_stock"],
                "target_scores": bt.CONFIG["target_scores"],
                "allowed_position_types": bt.CONFIG["allowed_position_types"],
                "exclude_preferred_stocks_sql": bt.CONFIG["exclude_preferred_stocks"],
                "local_additional_filter": "우C 로컬 제거",
                "enable_bear_market_filter": bt.CONFIG["enable_bear_market_filter"],
                "stop_loss_1_base_deviation_pct": bt.CONFIG["stop_loss_1_base_deviation_pct"],
                "target_strategy": bt.CONFIG["target_strategy"],
                "buy_commission_rate": bt.CONFIG["buy_commission_rate"],
                "sell_commission_rate": bt.CONFIG["sell_commission_rate"],
                "sell_tax_rate": bt.CONFIG["sell_tax_rate"],
                "slippage_rate": bt.CONFIG["slippage_rate"],
                "max_hold_days": bt.CONFIG["max_hold_days"],
            },
            "simulation_meta": sim_meta,
            "limitations": [
                "현재 상장 종목만 대상 (생존 편향)",
                "동일 종목 보유 중 재진입 불가",
                "호가 단위 스냅 후 체결 가정",
                "KOSPI proxy: DB 시가총액 합계 (KRX 로그인 부재)",
            ],
        },
        "statistics": stats,
        "extras": extras,
        "exit_reasons": analyze_exit_reasons(trades),
        "monthly": analyze_monthly(trades),
        "top_tickers_20": top_tickers_20(trades),
        "mdd_top3": mdd_top3_with_losers(pv_history, trades, n_losers=5),
        "current_candidates": load_current_candidates(),
    }

    bt.save_json_atomic(payload, OUT_JSON)
    report = render_report(payload)
    OUT_TXT.write_text(report, encoding="utf-8")

    ov = stats["overall"]
    bt.log.info(
        f"저장: {OUT_JSON.name} + {OUT_TXT.name} / {time.time()-t0:.1f}s / "
        f"거래 {ov.get('total_trades',0):,}건 / 누적 {ov.get('cumulative_return_pct',0):+.2f}% "
        f"/ CAGR {ov.get('cagr_pct',0):+.2f}% / MDD {ov.get('mdd_pct',0):.2f}% / "
        f"Sharpe {ov.get('sharpe',0):.2f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
