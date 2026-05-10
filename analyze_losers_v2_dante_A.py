"""v2_dante_A 손실 종목 전체 분석.

- backtesting 모듈 read-only 재시뮬 → 전체 trades 확보
- 종목별 집약 후 누적 손실(음수)만 필터
- 손실 크기 버킷 / 월별 손실 분포 / 반복 손실 종목 패턴 분석

저장: logs/v2_dante_A_losers_analysis.txt
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
import backtesting as bt  # read-only

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT_TXT = BASE / "logs" / "v2_dante_A_losers_analysis.txt"


def configure() -> None:
    """v2_dante_A (간단 스펙, 기존 기본 stage_amounts [100k/200k/400k/300k])"""
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


def days_between(a: str, b: str) -> int:
    da = datetime.strptime(a, "%Y%m%d")
    db = datetime.strptime(b, "%Y%m%d")
    return max(0, (db - da).days)


def main() -> int:
    configure()
    bt.log.info("=" * 70)
    bt.log.info("v2_dante_A 손실 종목 전체 분석")

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)
    kospi_regime = bt.load_kospi_regime(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    trades, _pv_hist, _meta = bt.simulate(df, bt.CONFIG, kospi_regime)
    del df

    bt.log.info(f"시뮬 완료 / {time.time()-t0:.1f}s / 거래 {len(trades):,}건")

    # ------------------------------ 종목별 집약 ------------------------------
    per_ticker: dict[str, dict] = {}
    for t in trades:
        d = per_ticker.setdefault(t.ticker, {
            "ticker": t.ticker,
            "name": t.name,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "total_cost": 0.0,
            "total_hold_days": 0,
            "close_months": [],   # 월별 손실 분포 집계용
            "individual_trades": [],  # 개별 거래 detail
        })
        d["trades"] += 1
        pnl = t.realized_pnl()
        d["total_pnl"] += pnl
        d["total_cost"] += t.total_cost
        if pnl > 0:
            d["wins"] += 1
        else:
            d["losses"] += 1
        if t.close_date:
            d["total_hold_days"] += days_between(t.open_date, t.close_date)
            d["close_months"].append(t.close_date[:6])
        d["individual_trades"].append({
            "open": t.open_date, "close": t.close_date,
            "pnl": pnl, "ret_pct": t.realized_pnl_pct(),
        })

    # 파생 계산
    for d in per_ticker.values():
        d["avg_return_pct"] = (
            round(d["total_pnl"] / d["total_cost"] * 100, 2) if d["total_cost"] > 0 else 0
        )
        d["win_rate_pct"] = round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0
        d["avg_hold_days"] = round(d["total_hold_days"] / d["trades"], 1) if d["trades"] else 0
        d["total_pnl_krw"] = round(d["total_pnl"], 0)

    # ------------------------------ 손실 종목 전체 ------------------------------
    losers = [d for d in per_ticker.values() if d["total_pnl_krw"] < 0]
    losers.sort(key=lambda x: x["total_pnl_krw"])  # 손실 많은 순

    # ------------------------------ 손실 구간 버킷 ------------------------------
    buckets = {
        "100만원 이상": [],
        "50~100만원": [],
        "10~50만원": [],
        "10만원 미만": [],
    }
    for d in losers:
        loss = -d["total_pnl_krw"]  # 양수화
        if loss >= 1_000_000:
            buckets["100만원 이상"].append(d)
        elif loss >= 500_000:
            buckets["50~100만원"].append(d)
        elif loss >= 100_000:
            buckets["10~50만원"].append(d)
        else:
            buckets["10만원 미만"].append(d)

    # ------------------------------ 반복 손실 종목 (3회+ 손실) ------------------------------
    repeat = [d for d in losers if d["trades"] >= 3]
    repeat.sort(key=lambda x: x["total_pnl_krw"])

    # ------------------------------ 월별 손실 분포 ------------------------------
    # close_date 기준 (손실 거래만)
    monthly_loss: dict[str, dict] = defaultdict(
        lambda: {"trades": 0, "loss_krw": 0.0, "tickers": set()}
    )
    for t in trades:
        if t.realized_pnl() >= 0 or not t.close_date:
            continue
        ym = t.close_date[:6]
        monthly_loss[ym]["trades"] += 1
        monthly_loss[ym]["loss_krw"] += t.realized_pnl()
        monthly_loss[ym]["tickers"].add(t.ticker)

    months_sorted = sorted(monthly_loss.keys())

    # ------------------------------ 거래 빈도별 통계 ------------------------------
    # 종목별 거래 수 → 평균 손실 pattern
    by_tradecount: dict[int, dict] = defaultdict(lambda: {"tickers": 0, "total_loss": 0.0})
    for d in losers:
        nt = d["trades"]
        by_tradecount[nt]["tickers"] += 1
        by_tradecount[nt]["total_loss"] += d["total_pnl_krw"]

    # ------------------------------ 리포트 작성 ------------------------------
    L = []
    L.append("=" * 100)
    L.append("  v2_dante_A 손실 종목 전체 분석")
    L.append(f"  기간: 2021-04-23 ~ 2026-04-22  /  생성: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"  전략: 점수 4점 + KOSPI 200MA + 중장기만 + 우선주 제외")
    L.append("=" * 100)

    # 요약
    total_tickers = len(per_ticker)
    winners_count = sum(1 for d in per_ticker.values() if d["total_pnl_krw"] > 0)
    losers_count = len(losers)
    break_even_count = sum(1 for d in per_ticker.values() if d["total_pnl_krw"] == 0)
    total_loss = sum(d["total_pnl_krw"] for d in losers)

    L.append("")
    L.append("[요약]")
    L.append(f"  총 거래된 종목            : {total_tickers:,} 종목")
    L.append(f"  수익 종목                 : {winners_count:,}  /  손실 종목: {losers_count:,}  /  무손익: {break_even_count:,}")
    L.append(f"  손실 종목 누적 손실 합    : {total_loss:,.0f}원")
    L.append(f"  종목당 평균 손실          : {total_loss/losers_count:,.0f}원" if losers_count else "")

    # [4] 손실 구간별 분류 (요약 먼저)
    L.append("")
    L.append("[4] 손실 구간별 분류")
    L.append("-" * 100)
    L.append(f"  {'구간':<18} {'종목 수':>8} {'구간 손실 합':>16} {'평균 손실':>14}")
    for label, items in buckets.items():
        if items:
            total = sum(d["total_pnl_krw"] for d in items)
            avg = total / len(items)
        else:
            total = 0.0
            avg = 0.0
        L.append(f"  {label:<18} {len(items):>8} {total:>+15,.0f} {avg:>+14,.0f}")

    # [3-A] 거래 빈도별 통계 (패턴 분석)
    L.append("")
    L.append("[3-A] 거래 빈도별 손실 패턴")
    L.append("-" * 100)
    L.append(f"  {'거래 횟수':>10} {'종목 수':>10} {'종목당 평균 손실':>20} {'합계 손실':>16}")
    for nt in sorted(by_tradecount.keys()):
        d = by_tradecount[nt]
        avg = d["total_loss"] / d["tickers"]
        L.append(f"  {nt:>10} {d['tickers']:>10} {avg:>+19,.0f} {d['total_loss']:>+15,.0f}")

    # [3-B] 반복 손실 종목 (3회+)
    L.append("")
    L.append("[3-B] 반복 손실 종목 (3회 이상 거래 + 누적 손실)")
    L.append("-" * 100)
    if not repeat:
        L.append("  해당 없음")
    else:
        L.append(f"  {'#':<3} {'종목':<22} {'거래':>5} {'승/패':>7} {'승률':>7} {'평균수익':>9} {'평균 보유':>9} {'누적 P&L':>15}")
        for i, d in enumerate(repeat, 1):
            L.append(
                f"  {i:<3} {d['ticker']} {d['name']:<17} {d['trades']:>5} "
                f"{d['wins']:>2}/{d['losses']:>2}  {d['win_rate_pct']:>6.2f}% "
                f"{d['avg_return_pct']:>+8.2f}% {d['avg_hold_days']:>7.1f}일 "
                f"{d['total_pnl_krw']:>+15,.0f}"
            )

    # [5] 월별 손실 분포
    L.append("")
    L.append("[5] 월별 손실 분포 (close_date 기준, 손실 거래만)")
    L.append("-" * 100)
    L.append(f"  {'월':<9} {'손실거래수':>10} {'손실 합계':>16} {'평균 손실':>14} {'종목 수':>8}")
    # 상위 10 월 (손실 큰 순)
    top_months = sorted(months_sorted, key=lambda m: monthly_loss[m]["loss_krw"])[:10]
    L.append("  ── 손실이 가장 큰 월 TOP 10 ──")
    for m in top_months:
        d = monthly_loss[m]
        avg = d["loss_krw"] / d["trades"] if d["trades"] else 0
        L.append(
            f"  {m[:4]}-{m[4:]:<4} {d['trades']:>10} {d['loss_krw']:>+15,.0f} "
            f"{avg:>+14,.0f} {len(d['tickers']):>8}"
        )
    L.append("")
    L.append("  ── 전체 월별 (연대기 순) ──")
    for m in months_sorted:
        d = monthly_loss[m]
        avg = d["loss_krw"] / d["trades"] if d["trades"] else 0
        L.append(
            f"  {m[:4]}-{m[4:]:<4} {d['trades']:>10} {d['loss_krw']:>+15,.0f} "
            f"{avg:>+14,.0f} {len(d['tickers']):>8}"
        )

    # [1] 손실 종목 전체 리스트 (손실 큰 순)
    L.append("")
    L.append("[1] 손실 종목 전체 리스트 (누적 손실 많은 순)")
    L.append("-" * 100)
    L.append(f"  총 {len(losers)}개 종목")
    L.append("")
    L.append(f"  {'#':<4} {'종목':<22} {'거래':>5} {'승/패':>7} {'승률':>7} {'평균수익':>9} {'평균보유':>9} {'누적 P&L':>15}")
    L.append("  " + "-" * 98)
    for i, d in enumerate(losers, 1):
        L.append(
            f"  {i:<4} {d['ticker']} {d['name']:<17} {d['trades']:>5} "
            f"{d['wins']:>2}/{d['losses']:>2}  {d['win_rate_pct']:>6.2f}% "
            f"{d['avg_return_pct']:>+8.2f}% {d['avg_hold_days']:>7.1f}일 "
            f"{d['total_pnl_krw']:>+15,.0f}"
        )

    # [2] 종합 인사이트
    L.append("")
    L.append("[종합 인사이트]")
    L.append("-" * 100)
    # 단발 손실 vs 반복 손실
    single = [d for d in losers if d["trades"] == 1]
    multi = [d for d in losers if d["trades"] >= 2]
    single_loss = sum(d["total_pnl_krw"] for d in single)
    multi_loss = sum(d["total_pnl_krw"] for d in multi)
    L.append(f"  · 단발 거래 손실 종목 : {len(single):>3}개, 합계 {single_loss:>+14,.0f}원 "
             f"(종목당 평균 {(single_loss/len(single)) if single else 0:>+12,.0f})")
    L.append(f"  · 복수 거래 손실 종목 : {len(multi):>3}개, 합계 {multi_loss:>+14,.0f}원 "
             f"(종목당 평균 {(multi_loss/len(multi)) if multi else 0:>+12,.0f})")
    # 대형 손실 월
    if top_months:
        worst_m = top_months[0]
        d = monthly_loss[worst_m]
        L.append(f"  · 최악의 월            : {worst_m[:4]}-{worst_m[4:]}  "
                 f"({d['trades']}거래, {d['loss_krw']:+,.0f}원 손실, {len(d['tickers'])}종목)")

    # 업종/테마 분석 — DB에 업종 정보 없음 → 표기만
    L.append("")
    L.append("  [업종/테마 분석]")
    L.append("    ⚠ 현재 stock_data.db 에 업종/테마 컬럼이 없어 체계적 업종 분석 불가.")
    L.append("    종목명 기반 관찰:")
    # 간단한 키워드 클러스터링
    kw_groups = {
        "건설/토건": ["건설", "토건"],
        "화학": ["화학", "케미칼", "화장품"],
        "전기/전자/반도체": ["전자", "반도체", "전기", "페타시스", "디엔에프", "SDI"],
        "바이오/제약": ["바이오", "제약", "의약", "HLB", "셀"],
        "자동차": ["자동차", "모터", "DI동일"],
        "금융": ["증권", "보험", "금융"],
        "에너지/조선": ["조선", "에너지", "중공업", "한화엔진"],
        "식품": ["식품", "사료", "흥아해운", "큐렉소"],
        "IT/SW": ["IT", "소프트", "테크", "정보"],
        "기타": [],
    }
    cluster_pnl: dict[str, list] = defaultdict(list)
    for d in losers:
        name = d["name"] or ""
        matched = False
        for group, kws in kw_groups.items():
            if group == "기타":
                continue
            for kw in kws:
                if kw in name:
                    cluster_pnl[group].append(d)
                    matched = True
                    break
            if matched:
                break
        if not matched:
            cluster_pnl["기타"].append(d)
    for g, arr in sorted(cluster_pnl.items(), key=lambda x: sum(d["total_pnl_krw"] for d in x[1])):
        tot = sum(d["total_pnl_krw"] for d in arr)
        L.append(f"    - {g:<20} {len(arr):>3}개 종목  누적 {tot:>+12,.0f}원")

    OUT_TXT.write_text("\n".join(L), encoding="utf-8")
    bt.log.info(f"저장: {OUT_TXT.name} / 총 {time.time()-t0:.1f}s / 손실 {losers_count}종목")
    return 0


if __name__ == "__main__":
    sys.exit(main())
