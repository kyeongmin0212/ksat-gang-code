"""v2_dante_A 손실 심층 분석.

재시뮬 + 각 거래의 진입 시점 지표/조건 + 매도 후 반등 여부 추적.

출력: logs/v2_dante_A_loss_reasons.txt
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import time
import math
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT_TXT = BASE / "logs" / "v2_dante_A_loss_reasons.txt"

# ============================================================
# v2_dante_A 설정
# ============================================================
def configure():
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


# ============================================================
# 조건 재계산 (특정 (date, ticker) 행 기준)
# ============================================================
def _ok(v):
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return True


def compute_conditions(row) -> dict:
    """df row → 8개 조건 True/False + score."""
    close = row.get("종가")
    low = row.get("저가")
    span_a = row.get("span_a_std")
    span_b = row.get("span_b_std")
    span_a_2x = row.get("span_a_2x")
    span_b_2x = row.get("span_b_2x")
    base = row.get("base_std")
    ma5 = row.get("ma5")
    ma20 = row.get("ma20")
    ma60 = row.get("ma60")
    bb_lower = row.get("bb_lower")
    vol_ratio = row.get("vol_ratio")

    c = {
        "cloud_above_std": _ok(close) and _ok(span_a) and _ok(span_b)
                           and close > max(span_a, span_b),
        "cloud_above_2x": _ok(close) and _ok(span_a_2x) and _ok(span_b_2x)
                          and close > max(span_a_2x, span_b_2x),
        "base_line_near": False,
        "ma_convergence": False,
        "volume_surge": _ok(vol_ratio) and vol_ratio >= 2.0,
        "accumulation_bar": _ok(vol_ratio) and vol_ratio >= 3.0,
        "bb_lower_touch": _ok(low) and _ok(bb_lower) and low <= bb_lower,
        "base_line_not_overheated": False,
    }
    if _ok(close) and _ok(base) and close > 0:
        dev = abs(close - base) / close
        c["base_line_near"] = dev <= 0.02
        c["base_line_not_overheated"] = dev < 0.07
    if _ok(ma5) and _ok(ma20) and _ok(ma60):
        mas = [ma5, ma20, ma60]
        mn, mx = min(mas), max(mas)
        if mn > 0:
            c["ma_convergence"] = (mx - mn) / mn <= 0.02
    score = sum(1 for v in c.values() if v)
    return {"conditions": c, "score": score}


# ============================================================
# 보조 유틸
# ============================================================
def share_weighted_reason(pos: bt.Position) -> str:
    if not pos.sell_events:
        return "미체결"
    bucket = {}
    for ev in pos.sell_events:
        bucket[ev["reason"]] = bucket.get(ev["reason"], 0) + ev["shares"]
    return max(bucket.items(), key=lambda x: x[1])[0]


def cond_symbol(v: bool) -> str:
    return "✓" if v else "·"


# ============================================================
# 메인
# ============================================================
def main() -> int:
    configure()
    bt.log.info("=" * 70)
    bt.log.info("v2_dante_A 손실 심층 분석 (조건/사유/재매수)")

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)
    kospi_regime = bt.load_kospi_regime(
        bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG
    )
    trades, _pv, _meta = bt.simulate(df, bt.CONFIG, kospi_regime)

    bt.log.info(f"시뮬 완료 / {time.time()-t0:.1f}s / 거래 {len(trades):,}건")

    # 룩업: (날짜, 종목코드) → row dict
    bt.log.info("(날짜, 종목) 룩업 테이블 구성")
    row_lookup: dict[tuple[str, str], dict] = {}
    for rec in df.to_dict("records"):
        row_lookup[(rec["날짜"], rec["종목코드"])] = rec

    # 종목별 거래 순서대로
    by_ticker: dict[str, list[bt.Position]] = defaultdict(list)
    for t in trades:
        by_ticker[t.ticker].append(t)
    for lst in by_ticker.values():
        lst.sort(key=lambda t: t.open_date)

    # 거래별 상세 정보 생성
    trade_records: list[dict] = []
    for t in trades:
        reason = share_weighted_reason(t)
        is_loss = t.realized_pnl() < 0

        entry_row = row_lookup.get((t.open_signal_date, t.ticker))
        cond_info = compute_conditions(entry_row) if entry_row else {"conditions": {}, "score": None}

        # 보유일
        if t.close_date:
            hold_days = (datetime.strptime(t.close_date, "%Y%m%d")
                         - datetime.strptime(t.open_date, "%Y%m%d")).days
        else:
            hold_days = None

        # 매도 후 30거래일 고가 추적
        post_max = None
        if t.close_date and is_loss:
            try:
                ticker_rows = df[(df["종목코드"] == t.ticker) & (df["날짜"] > t.close_date)]\
                              .sort_values("날짜").head(30)
                if len(ticker_rows) > 0:
                    post_max = int(ticker_rows["고가"].max())
            except Exception:
                post_max = None

        # 매수 시가 (실제 체결가)
        entry_exec_price = 0.0
        if t.stage_shares and t.stage_shares[0] > 0 and t.stage_costs[0] > 0:
            entry_exec_price = t.stage_costs[0] / t.stage_shares[0]

        # 매도 최종가
        exit_price = 0
        exit_shares = 0
        if t.sell_events:
            last = t.sell_events[-1]
            exit_price = last["price"]
            for ev in t.sell_events:
                exit_shares += ev["shares"]

        trade_records.append({
            "ticker": t.ticker,
            "name": t.name,
            "score": cond_info["score"],
            "conditions": cond_info["conditions"],
            "open_signal_date": t.open_signal_date,
            "open_date": t.open_date,
            "close_date": t.close_date,
            "hold_days": hold_days,
            "pnl": t.realized_pnl(),
            "pnl_pct": t.realized_pnl_pct(),
            "reason": reason,
            "is_loss": is_loss,
            "entry_exec_price": entry_exec_price,
            "exit_price": exit_price,
            "post_exit_max_price": post_max,
            "kospi_bull_at_entry": kospi_regime.get(t.open_signal_date, True) if kospi_regime else None,
            "sl1": t.sl1,
            "sl2": t.sl2,
            "target": t.target,
            "stages_filled": sum(1 for f in t.stage_filled if f),
            "total_bought_shares": t.total_bought_shares,
            "total_cost": t.total_cost,
        })

    losers = [r for r in trade_records if r["is_loss"]]
    winners = [r for r in trade_records if not r["is_loss"]]

    # --------- A-1. 매도 사유별 통계 + 반등 여부 ---------
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for r in losers:
        by_reason[r["reason"]].append(r)

    # --------- A-2. 조건 빈도 (손실 vs 승리 비교) ---------
    cond_names = ["cloud_above_std", "cloud_above_2x", "base_line_near",
                  "ma_convergence", "volume_surge", "accumulation_bar",
                  "bb_lower_touch", "base_line_not_overheated"]
    cond_freq_loss = {c: 0 for c in cond_names}
    cond_freq_win = {c: 0 for c in cond_names}
    for r in losers:
        for c in cond_names:
            if r["conditions"].get(c):
                cond_freq_loss[c] += 1
    for r in winners:
        for c in cond_names:
            if r["conditions"].get(c):
                cond_freq_win[c] += 1

    # --------- A-3. KOSPI regime 분포 ---------
    kospi_bull_loss = sum(1 for r in losers if r["kospi_bull_at_entry"])
    kospi_bear_loss = sum(1 for r in losers if r["kospi_bull_at_entry"] is False)
    kospi_bull_win = sum(1 for r in winners if r["kospi_bull_at_entry"])
    kospi_bear_win = sum(1 for r in winners if r["kospi_bull_at_entry"] is False)

    # --------- A-1 손절 후 반등 (sl2 손절만 대상) ---------
    sl2_trades = by_reason.get("2차손절", [])
    post_recovery = []
    for r in sl2_trades:
        if r["post_exit_max_price"] and r["entry_exec_price"] > 0:
            rec_pct = (r["post_exit_max_price"] - r["entry_exec_price"]) / r["entry_exec_price"] * 100
            post_recovery.append({
                "ticker": r["ticker"],
                "entry_exec": r["entry_exec_price"],
                "exit_price": r["exit_price"],
                "post_max": r["post_exit_max_price"],
                "post_ret_pct_vs_entry": rec_pct,
                "hold_days": r["hold_days"],
            })
    n_recovered = sum(1 for p in post_recovery if p["post_ret_pct_vs_entry"] > 0)
    n_still_down = len(post_recovery) - n_recovered

    # --------- B. 재매수 분석 ---------
    # 같은 종목 연속 거래 간격
    repeat_gaps: list[dict] = []
    for tkr, lst in by_ticker.items():
        if len(lst) < 2:
            continue
        # 모든 거래가 손실인지?
        all_loss = all(t.realized_pnl() < 0 for t in lst)
        for i in range(1, len(lst)):
            prev = lst[i - 1]
            curr = lst[i]
            if not prev.close_date:
                continue
            gap = (datetime.strptime(curr.open_date, "%Y%m%d")
                   - datetime.strptime(prev.close_date, "%Y%m%d")).days
            repeat_gaps.append({
                "ticker": tkr,
                "name": curr.name,
                "prev_close_date": prev.close_date,
                "prev_pnl": prev.realized_pnl(),
                "prev_reason": share_weighted_reason(prev),
                "new_entry_date": curr.open_date,
                "gap_days": gap,
                "new_pnl": curr.realized_pnl(),
                "all_loss_streak": all_loss,
            })

    # --------- C. 케이스 스터디 데이터 준비 ---------
    case_tickers = ["004380", "004090", "085670"]  # 삼익THK, 한국석유, 뉴프렉스
    case_data = {}
    for tkr in case_tickers:
        entries = []
        for t in by_ticker.get(tkr, []):
            entry_row = row_lookup.get((t.open_signal_date, tkr))
            cond = compute_conditions(entry_row) if entry_row else {"conditions": {}, "score": None}
            # 매수 10일 전 ~ 매도 10일 후 가격 추이
            if t.close_date:
                try:
                    window = df[(df["종목코드"] == tkr)]\
                        .sort_values("날짜")
                    open_idx = window[window["날짜"] == t.open_signal_date].index
                    if len(open_idx) > 0:
                        pos_idx = window.index.get_loc(open_idx[0])
                        start_idx = max(0, pos_idx - 10)
                        close_pos = window[window["날짜"] == t.close_date].index
                        if len(close_pos) > 0:
                            close_idx = window.index.get_loc(close_pos[0])
                            end_idx = min(len(window), close_idx + 11)
                        else:
                            end_idx = min(len(window), pos_idx + 30)
                        sub = window.iloc[start_idx:end_idx]
                        chart = [(r["날짜"], r["종가"]) for _, r in sub.iterrows()]
                    else:
                        chart = []
                except Exception:
                    chart = []
            else:
                chart = []
            entries.append({
                "open_signal": t.open_signal_date,
                "open_date": t.open_date,
                "close_date": t.close_date,
                "entry_exec": t.stage_costs[0] / max(1, t.stage_shares[0]) if t.stage_shares[0] else 0,
                "sl1": t.sl1,
                "sl2": t.sl2,
                "target": t.target,
                "reason": share_weighted_reason(t),
                "pnl": t.realized_pnl(),
                "pnl_pct": t.realized_pnl_pct(),
                "score": cond["score"],
                "conditions": cond["conditions"],
                "stages_filled": sum(1 for f in t.stage_filled if f),
                "hold_days": (datetime.strptime(t.close_date, "%Y%m%d")
                              - datetime.strptime(t.open_date, "%Y%m%d")).days
                             if t.close_date else None,
                "chart": chart,
            })
        case_data[tkr] = entries

    # ============================================================
    # 리포트
    # ============================================================
    L = []
    L.append("=" * 100)
    L.append("  v2_dante_A 손실 심층 분석")
    L.append(f"  기간: 2021-04-23 ~ 2026-04-22  /  생성: {datetime.now().isoformat(timespec='seconds')}")
    L.append("=" * 100)

    L.append("")
    L.append(f"[요약] 거래 총 {len(trade_records):,}건 (승 {len(winners)} / 패 {len(losers)})")
    L.append(f"       패(손실) 중 '2차손절' {len(by_reason.get('2차손절', []))}건  "
             f"'1차손절' {len(by_reason.get('1차손절', []))}건  "
             f"'기간초과' {len(by_reason.get('기간초과', []))}건")

    # ============================================================
    # A. 손실 난 이유
    # ============================================================
    L.append("")
    L.append("━" * 100)
    L.append("A. 손실 난 이유 — 매도 사유별 상세 분석")
    L.append("━" * 100)

    # A-1. 사유별 기본 통계
    L.append("")
    L.append("[A-1] 손실 거래 사유별 통계")
    L.append(f"  {'사유':<12} {'건수':>6} {'평균 보유일':>12} {'평균 손실률':>12} {'평균 손실액':>16}")
    for rsn, items in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        hold_avg = np.mean([r["hold_days"] for r in items if r["hold_days"] is not None]) if items else 0
        ret_avg = np.mean([r["pnl_pct"] for r in items])
        pnl_avg = np.mean([r["pnl"] for r in items])
        L.append(f"  {rsn:<12} {len(items):>6} {hold_avg:>11.1f}일 "
                 f"{ret_avg:>+11.2f}% {pnl_avg:>+15,.0f}")

    # A-1b. 매도 후 반등 여부 (2차손절만)
    L.append("")
    L.append("[A-1b] 2차손절 후 30거래일 고가 추적 — '손절 후 반등' 분석")
    L.append(f"  2차손절 총 {len(sl2_trades)}건 중 추적 가능 {len(post_recovery)}건")
    L.append(f"    · 매도 후 30일 내 매수가 회복한 경우: {n_recovered}건 ({100*n_recovered/max(1,len(post_recovery)):.1f}%)")
    L.append(f"    · 매도 후에도 계속 하락 유지: {n_still_down}건 ({100*n_still_down/max(1,len(post_recovery)):.1f}%)")
    if post_recovery:
        # 회복된 상위 10 (잘못 잘랐을 가능성)
        recovered_sorted = sorted(
            [p for p in post_recovery if p["post_ret_pct_vs_entry"] > 0],
            key=lambda x: -x["post_ret_pct_vs_entry"],
        )[:10]
        L.append("")
        L.append("  ── 손절 후 매수가 대비 크게 반등한 TOP 10 (잘못 자른 것) ──")
        L.append(f"  {'종목':<10} {'매수가':>10} {'손절가':>10} {'30일 최대':>10} {'매수가 대비 반등':>20}")
        for p in recovered_sorted:
            L.append(f"  {p['ticker']:<10} {p['entry_exec']:>10,.0f} "
                     f"{p['exit_price']:>10,} {p['post_max']:>10,} "
                     f"{p['post_ret_pct_vs_entry']:>+18.2f}%")

    # A-2. 손실 vs 승리 조건 구성 차이
    L.append("")
    L.append("[A-2] 진입 시 조건 빈도 비교 — 승리 vs 손실 (같은 4점이어도 내용이 다르다)")
    L.append(f"  {'조건':<28} {'승리 (N={})'.format(len(winners)):>15} {'손실 (N={})'.format(len(losers)):>15} {'승-손 차이':>15}")
    for c in cond_names:
        w = cond_freq_win[c]
        l = cond_freq_loss[c]
        w_pct = w / len(winners) * 100 if winners else 0
        l_pct = l / len(losers) * 100 if losers else 0
        L.append(f"  {c:<28} {w_pct:>14.1f}% {l_pct:>14.1f}% {w_pct-l_pct:>+14.1f}%p")

    # A-3. KOSPI regime
    L.append("")
    L.append("[A-3] 진입 시점 KOSPI 시장 상황")
    L.append(f"  승리 진입: bull {kospi_bull_win} / bear {kospi_bear_win}  "
             f"(bull 비율 {100*kospi_bull_win/max(1,len(winners)):.1f}%)")
    L.append(f"  손실 진입: bull {kospi_bull_loss} / bear {kospi_bear_loss}  "
             f"(bull 비율 {100*kospi_bull_loss/max(1,len(losers)):.1f}%)")
    L.append("  ※ v2_dante_A는 약세장 필터 적용 중이므로 모든 진입이 bull 상태여야 함.")
    L.append("     만약 bear가 0이면 개별 종목 추세와 KOSPI 추세가 괴리 있다는 뜻")

    # ============================================================
    # B. 재매수 이유 분석
    # ============================================================
    L.append("")
    L.append("━" * 100)
    L.append("B. 재매수 이유 분석")
    L.append("━" * 100)

    # B-1. 재매수 간격 통계
    if repeat_gaps:
        gaps_loss_only = [g["gap_days"] for g in repeat_gaps if g["all_loss_streak"]]
        gaps_all = [g["gap_days"] for g in repeat_gaps]
        L.append("")
        L.append("[B-1] 같은 종목 재매수 간격 (직전 매도 → 다음 매수)")
        L.append(f"  전체 재매수 이벤트: {len(gaps_all)}건")
        if gaps_all:
            L.append(f"    중앙값: {int(np.median(gaps_all))}일 / 평균: {np.mean(gaps_all):.1f}일")
            L.append(f"    최단: {min(gaps_all)}일  /  최장: {max(gaps_all)}일")
        L.append(f"  모두 손실 연패인 종목의 재매수: {len(gaps_loss_only)}건")
        if gaps_loss_only:
            L.append(f"    중앙값: {int(np.median(gaps_loss_only))}일 / 평균: {np.mean(gaps_loss_only):.1f}일")

        # 구간별 분포
        bins = [("1주 이내 (≤7일)", 0, 7),
                ("1~4주 (8~30일)", 8, 30),
                ("1~3개월 (31~90일)", 31, 90),
                ("3~6개월 (91~180일)", 91, 180),
                ("6개월 초과 (>180일)", 181, 99999)]
        L.append("")
        L.append("  재매수 간격 분포 (전체 재매수):")
        for lbl, lo, hi in bins:
            n = sum(1 for g in gaps_all if lo <= g <= hi)
            L.append(f"    {lbl:<25} {n:>5}건 ({100*n/max(1,len(gaps_all)):.1f}%)")

    # B-2. 손실 연패 종목의 재매수 패턴
    L.append("")
    L.append("[B-2] '신호 함정' 분석 — 반복 손실 종목의 재매수 기간")
    streak_tickers = [tkr for tkr, lst in by_ticker.items()
                     if len(lst) >= 3 and all(t.realized_pnl() < 0 for t in lst)]
    L.append(f"  3회 이상 거래 && 모두 손실인 종목: {len(streak_tickers)}개")
    for tkr in streak_tickers[:10]:
        lst = by_ticker[tkr]
        gaps_this = []
        for i in range(1, len(lst)):
            if lst[i-1].close_date:
                g = (datetime.strptime(lst[i].open_date, "%Y%m%d")
                     - datetime.strptime(lst[i-1].close_date, "%Y%m%d")).days
                gaps_this.append(g)
        dates_summary = " → ".join(f"{t.open_date}(패)" for t in lst)
        gap_str = " / ".join(f"{g}일" for g in gaps_this)
        L.append(f"    · {tkr} {lst[0].name:<18} {len(lst)}회  간격: {gap_str}")
        L.append(f"        진입일: {dates_summary}")

    # B-3. 시사점
    L.append("")
    L.append("[B-3] '신호 함정' 원리 (왜 같은 종목이 반복해서 신호를 내는가)")
    L.append("  1. score=4 신호는 지표의 일정 패턴(예: 기준선 근접 + 이평 정배열 부재 상황)에서")
    L.append("     '회복 시도' 구간에 자주 재출현. 하락 후 반등 흉내내다가 다시 구름 이탈.")
    L.append("  2. 시스템이 과거 실패 학습 없음 — 매번 같은 조건 True면 똑같이 진입.")
    L.append("  3. 실패 연속 종목은 기저 추세가 약하거나 시총·유동성 문제 가능.")
    L.append("  4. 해법: '최근 N거래 중 M회 손절 시 재매수 금지' 블랙리스트 규칙 추가.")

    # ============================================================
    # C. 케이스 스터디
    # ============================================================
    L.append("")
    L.append("━" * 100)
    L.append("C. 실제 사례 상세 분석")
    L.append("━" * 100)

    case_titles = {
        "004380": "삼익THK (4전 4패)",
        "004090": "한국석유 (4전 4패)",
        "085670": "뉴프렉스 (2전 2패, -477k)",
    }

    for tkr, title in case_titles.items():
        entries = case_data.get(tkr, [])
        L.append("")
        L.append("─" * 100)
        L.append(f"【케이스】 {tkr} — {title}")
        L.append("─" * 100)
        if not entries:
            L.append("  (거래 데이터 없음)")
            continue

        for idx, e in enumerate(entries, 1):
            L.append(f"\n  ─ 거래 #{idx} ─")
            L.append(f"    신호일/매수일    : {e['open_signal']} / {e['open_date']}")
            L.append(f"    매도일/보유일    : {e['close_date']} / {e['hold_days']}일")
            L.append(f"    진입 점수        : {e['score']}")
            # 조건 체크
            cond_str = "    진입 조건 체크   :"
            for c in cond_names:
                cond_str += f" {c[:13]}{cond_symbol(e['conditions'].get(c))}"
            L.append(cond_str)
            L.append(f"    1차 체결가/손절1/손절2/목표 : "
                     f"{e['entry_exec']:,.0f} / {e['sl1']:,} / {e['sl2']:,} / {e['target']:,}")
            L.append(f"    체결 스테이지    : {e['stages_filled']}/4")
            L.append(f"    매도 사유        : {e['reason']}  "
                     f"  손익: {e['pnl']:+,.0f}원 ({e['pnl_pct']:+.2f}%)")

            # 가격 chart (text)
            if e["chart"]:
                L.append("    가격 추이 (매수 10일전 ~ 매도 10일후):")
                closes = [c[1] for c in e["chart"]]
                p_min, p_max = min(closes), max(closes)
                spread = p_max - p_min if p_max > p_min else 1
                for ds, cp in e["chart"]:
                    pos = int((cp - p_min) / spread * 50)
                    marker = ""
                    if ds == e["open_signal"]:
                        marker = " ← 신호일"
                    elif ds == e["open_date"]:
                        marker = " ← 매수"
                    elif ds == e["close_date"]:
                        marker = " ← 매도"
                    bar_s = " " * pos + "█"
                    L.append(f"      {ds}  {cp:>10,}  {bar_s}{marker}")

        # 재매수 간격
        if len(entries) >= 2:
            L.append(f"\n  ─ 재매수 간격 ─")
            for i in range(1, len(entries)):
                if entries[i-1]["close_date"]:
                    g = (datetime.strptime(entries[i]["open_date"], "%Y%m%d")
                         - datetime.strptime(entries[i-1]["close_date"], "%Y%m%d")).days
                    L.append(f"    #{i} 매도({entries[i-1]['close_date']}) → #{i+1} 매수({entries[i]['open_date']}) = {g}일")

        # 분석 요지
        L.append("\n  ─ 분석 요지 ─")
        total_pnl = sum(e["pnl"] for e in entries)
        avg_hold = np.mean([e["hold_days"] for e in entries if e["hold_days"]])
        reasons = Counter(e["reason"] for e in entries)
        L.append(f"    총 {len(entries)}회 거래 / 누적 손실 {total_pnl:+,.0f}원 / 평균 보유 {avg_hold:.1f}일")
        L.append(f"    매도 사유 분포: {dict(reasons)}")
        # 공통 조건
        common_cond = [c for c in cond_names if all(e["conditions"].get(c) for e in entries)]
        L.append(f"    모든 진입에서 공통으로 True인 조건: {common_cond if common_cond else '(없음)'}")

    OUT_TXT.write_text("\n".join(L), encoding="utf-8")
    bt.log.info(f"저장: {OUT_TXT.name} / {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
