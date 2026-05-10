"""v4 보유 기간 상세 분석."""
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
import backtesting as bt

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT_TXT = BASE / "logs" / "v4_holding_period_analysis.txt"


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
    # v4
    bt.CONFIG["require_base_line_near"] = True
    bt.CONFIG["require_not_overheated_entry"] = True
    bt.CONFIG["re_entry_cooldown_days"] = 30
    bt.CONFIG["blacklist_enabled"] = True
    bt.CONFIG["blacklist_lookback"] = 5
    bt.CONFIG["blacklist_threshold"] = 3
    bt.CONFIG["blacklist_ban_days"] = 252


def days_between(a: str, b: str) -> int:
    da = datetime.strptime(a, "%Y%m%d")
    db = datetime.strptime(b, "%Y%m%d")
    return max(0, (db - da).days)


def share_weighted_reason(pos: bt.Position) -> str:
    if not pos.sell_events:
        return "미체결"
    bucket: dict[str, int] = {}
    for ev in pos.sell_events:
        bucket[ev["reason"]] = bucket.get(ev["reason"], 0) + ev["shares"]
    return max(bucket.items(), key=lambda x: x[1])[0]


def stats(arr):
    if not arr:
        return None
    return {
        "n": len(arr),
        "avg": round(float(np.mean(arr)), 1),
        "median": int(np.median(arr)),
        "min": int(np.min(arr)),
        "max": int(np.max(arr)),
    }


def main() -> int:
    configure()
    bt.log.info("v4 재시뮬 + 보유 기간 분석")

    t0 = time.time()
    df = bt.load_merged_data(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    df = bt.compute_rolling_stats(df, bt.CONFIG)
    df = bt.compute_signals(df, bt.CONFIG)
    kospi = bt.load_kospi_regime(bt.CONFIG["start_date"], bt.CONFIG["end_date"], bt.CONFIG)
    trades, _pv, _meta = bt.simulate(df, bt.CONFIG, kospi)
    del df

    bt.log.info(f"시뮬 완료 {time.time()-t0:.1f}s / 거래 {len(trades)}건")

    # 각 거래: (hold_days, reason, pnl_pct, position_type)
    recs = []
    for t in trades:
        if not t.close_date:
            continue
        recs.append({
            "hold": days_between(t.open_date, t.close_date),
            "reason": share_weighted_reason(t),
            "pnl_pct": t.realized_pnl_pct(),
            "position_type": t.position_type,
            "is_win": t.realized_pnl() > 0,
        })

    # 전체
    all_hold = [r["hold"] for r in recs]

    # 사유별
    by_reason: dict[str, list] = defaultdict(list)
    for r in recs:
        by_reason[r["reason"]].append(r["hold"])

    # 포지션별
    by_ptype: dict[str, list] = defaultdict(list)
    for r in recs:
        by_ptype[r["position_type"]].append(r["hold"])

    # 보유 기간 분포
    buckets = [
        ("1주 이내 (≤7일)",       lambda h: h <= 7),
        ("1~2주 (8~14일)",       lambda h: 8 <= h <= 14),
        ("2~4주 (15~28일)",      lambda h: 15 <= h <= 28),
        ("1~2개월 (29~60일)",    lambda h: 29 <= h <= 60),
        ("2~3개월 (61~90일)",    lambda h: 61 <= h <= 90),
        ("3개월 이상 (91일+)",    lambda h: h >= 91),
    ]
    bucket_counts = []
    for label, pred in buckets:
        cnt = sum(1 for h in all_hold if pred(h))
        bucket_counts.append((label, cnt, 100 * cnt / max(1, len(all_hold))))

    # 수익 크기별
    size_groups = [
        ("대박 (+50% 이상)",      lambda p: p >= 50),
        ("큰 수익 (+20~50%)",    lambda p: 20 <= p < 50),
        ("작은 수익 (0~20%)",    lambda p: 0 < p < 20),
        ("손익분기 (0%)",         lambda p: p == 0),
        ("작은 손실 (0~-10%)",   lambda p: -10 < p < 0),
        ("큰 손실 (-10% 이하)",   lambda p: p <= -10),
    ]
    size_stats = []
    for label, pred in size_groups:
        arr = [r["hold"] for r in recs if pred(r["pnl_pct"])]
        size_stats.append((label, stats(arr)))

    # 리포트
    L = []
    L.append("=" * 90)
    L.append("  v4 보유 기간 상세 분석")
    L.append(f"  기간: {bt.CONFIG['start_date']} ~ {bt.CONFIG['end_date']}  /  생성: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"  총 거래: {len(recs):,}건")
    L.append("=" * 90)

    # [1] 전체
    L.append("")
    L.append("[1] 전체 보유 기간 (달력일 기준)")
    s = stats(all_hold)
    L.append(f"  {'지표':<12} {'값':>8}")
    L.append("  " + "-" * 22)
    L.append(f"  {'평균':<12} {s['avg']:>7}일")
    L.append(f"  {'중앙값':<12} {s['median']:>7}일")
    L.append(f"  {'최소':<12} {s['min']:>7}일")
    L.append(f"  {'최대':<12} {s['max']:>7}일")

    # [2] 사유별
    L.append("")
    L.append("[2] 매도 사유별 평균 보유일")
    L.append(f"  {'사유':<12} {'건수':>6} {'평균':>8} {'중앙값':>8} {'최소':>6} {'최대':>6}")
    L.append("  " + "-" * 55)
    for rsn, arr in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        s = stats(arr)
        L.append(f"  {rsn:<12} {s['n']:>6} {s['avg']:>7}일 {s['median']:>7}일 {s['min']:>5}일 {s['max']:>5}일")

    # [3] 포지션별
    L.append("")
    L.append("[3] 포지션 타입별 평균 보유일")
    L.append(f"  {'타입':<14} {'건수':>6} {'평균':>8} {'중앙값':>8}")
    L.append("  " + "-" * 42)
    for pt, arr in sorted(by_ptype.items(), key=lambda x: -len(x[1])):
        s = stats(arr)
        L.append(f"  {pt:<14} {s['n']:>6} {s['avg']:>7}일 {s['median']:>7}일")

    # [4] 보유 기간 분포
    L.append("")
    L.append("[4] 보유 기간 분포")
    L.append(f"  {'구간':<22} {'건수':>6} {'비중':>7}")
    L.append("  " + "-" * 42)
    for label, cnt, pct in bucket_counts:
        L.append(f"  {label:<22} {cnt:>6} {pct:>6.2f}%")

    # [5] 수익 크기별
    L.append("")
    L.append("[5] 수익 크기별 평균 보유일")
    L.append(f"  {'수익 크기':<22} {'건수':>6} {'평균':>8} {'중앙값':>8} {'최소':>6} {'최대':>6}")
    L.append("  " + "-" * 65)
    for label, s in size_stats:
        if s is None:
            L.append(f"  {label:<22} {'0':>6} {'-':>8} {'-':>8} {'-':>6} {'-':>6}")
        else:
            L.append(
                f"  {label:<22} {s['n']:>6} {s['avg']:>7}일 {s['median']:>7}일 "
                f"{s['min']:>5}일 {s['max']:>5}일"
            )

    # 승/패별 보유일 (추가)
    win_hold = [r["hold"] for r in recs if r["is_win"]]
    lose_hold = [r["hold"] for r in recs if not r["is_win"]]
    L.append("")
    L.append("[+] 승/패별 평균 보유일")
    L.append(f"  승리: {stats(win_hold)}")
    L.append(f"  패배: {stats(lose_hold)}")

    OUT_TXT.write_text("\n".join(L), encoding="utf-8")
    bt.log.info(f"저장: {OUT_TXT.name} / {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
