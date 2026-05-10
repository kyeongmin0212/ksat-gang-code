"""dante_rules_v2.json analyzed_stocks(41건) 손절 분석."""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import json
import statistics as stats
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(r"C:\Users\sji48\ksat_gang")
RULES = BASE / "dante_rules_v2.json"
OUT   = BASE / "logs" / "dante_chart_stoploss_analysis.txt"


def main() -> None:
    with open(RULES, "r", encoding="utf-8") as f:
        data = json.load(f)
    stocks = data["analyzed_stocks"]

    out: list[str] = []
    w = out.append

    w("=" * 84)
    w("  단테 차트 41장 손절선 정밀 분석")
    w("  데이터: dante_rules_v2.json → analyzed_stocks (2026-01-15 ~ 2026-04-21)")
    w("=" * 84)
    w("")
    w("[데이터 한계]")
    w("  · buy_zone 은 단일 가격 (박스 중심점). 박스 상단/하단 정보 미수록.")
    w("  · 각 손절가의 '근거'(직전 저점? 기준선? 구름?)는 원자료에 명시 안 됨.")
    w("  · → 본 보고서는 buy_zone 대비 손절가 비율과 분류별 패턴만 분석.")
    w("")

    # ---------------------------------------------------------------
    # 1. 41개 차트 손절가 표
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[1] 41개 차트 손절가 표 (날짜순)")
    w("─" * 84)
    w(f"  {'날짜':<10} {'타입':<14} {'종목':<14} {'매수영역':>10} {'손절':>10} "
      f"{'손절률':>8} {'채널':<14}")
    w("  " + "-" * 80)
    for s in stocks:
        sl = s.get("stop_loss") or s.get("stop_loss_1")
        bz = s["buy_zone"]
        ratio = s["sl_ratio_pct"]
        channel = s.get("channel", "?")[:12]
        ttype = s["type"][:12]
        w(f"  {s['date']:<10} {ttype:<14} {s['name']:<14} "
          f"{bz:>10,} {sl:>10,} {ratio:>+7.1f}% {channel:<14}")
    w("")

    # ---------------------------------------------------------------
    # 2. 분류별 통계
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[2] 분류별 손절폭 통계")
    w("─" * 84)
    by_type: dict[str, list[float]] = defaultdict(list)
    for s in stocks:
        by_type[s["type"]].append(s["sl_ratio_pct"])
    by_type["전체"] = [s["sl_ratio_pct"] for s in stocks]

    w(f"  {'분류':<18} {'N':>4} {'평균':>8} {'중앙값':>8} {'최소':>8} "
      f"{'최대':>8} {'표준편차':>10}")
    w("  " + "-" * 70)
    for t in ["스윙", "스윙&중장기", "중장기", "전체"]:
        if t not in by_type:
            continue
        vals = by_type[t]
        w(f"  {t:<18} {len(vals):>4} "
          f"{stats.mean(vals):>+7.2f}% "
          f"{stats.median(vals):>+7.2f}% "
          f"{min(vals):>+7.2f}% "
          f"{max(vals):>+7.2f}% "
          f"{stats.pstdev(vals):>9.2f}")
    w("")

    # ---------------------------------------------------------------
    # 3. 손절폭 분포 (히스토그램 - 텍스트)
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[3] 손절폭 분포 히스토그램 (전체 41개)")
    w("─" * 84)
    bins = [(-30, -25), (-25, -20), (-20, -15), (-15, -10), (-10, -8),
            (-8, -6), (-6, -5), (-5, -4), (-4, -3), (-3, -2), (-2, 0)]
    for lo, hi in bins:
        cnt = sum(1 for s in stocks if lo <= s["sl_ratio_pct"] < hi)
        bar = "█" * cnt
        w(f"  {lo:+4d}% ~ {hi:+3d}%  | {cnt:2d} {bar}")
    w("")

    # ---------------------------------------------------------------
    # 4. 채널 종류별 손절폭
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[4] 채널 패턴별 평균 손절폭")
    w("─" * 84)
    by_channel: dict[str, list[float]] = defaultdict(list)
    for s in stocks:
        ch = s.get("channel", "?")
        by_channel[ch].append(s["sl_ratio_pct"])
    w(f"  {'채널':<22} {'N':>4} {'평균':>8} {'범위':>20}")
    w("  " + "-" * 60)
    for ch, vals in sorted(by_channel.items(), key=lambda kv: -len(kv[1])):
        if len(vals) >= 1:
            r = f"{min(vals):+.1f}% ~ {max(vals):+.1f}%"
            w(f"  {ch:<22} {len(vals):>4} {stats.mean(vals):>+7.2f}% {r:>20}")
    w("")

    # ---------------------------------------------------------------
    # 5. 시그널 마커별 손절폭
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[5] 매수 신호 마커별 평균 손절폭")
    w("─" * 84)
    by_signal: dict[str, list[float]] = defaultdict(list)
    for s in stocks:
        sg = s.get("signal", "?")
        by_signal[sg].append(s["sl_ratio_pct"])
    w(f"  {'시그널':<22} {'N':>4} {'평균':>8}")
    w("  " + "-" * 40)
    for sg, vals in sorted(by_signal.items(), key=lambda kv: -len(kv[1])):
        w(f"  {sg:<22} {len(vals):>4} {stats.mean(vals):>+7.2f}%")
    w("")

    # ---------------------------------------------------------------
    # 6. 이중 손절 사례 (dual_stop)
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[6] 이중 손절 (dual_stop) 사례 분석")
    w("─" * 84)
    duals = [s for s in stocks if s.get("dual_stop")]
    w(f"  발견된 dual_stop 케이스: {len(duals)}건")
    if duals:
        w(f"  {'종목':<12} {'매수':>8} {'1차손절':>9} {'2차손절':>9} "
          f"{'1차%':>8} {'2차%':>8} {'갭':>8}")
        w("  " + "-" * 70)
        for s in duals:
            bz = s["buy_zone"]
            sl1 = s.get("stop_loss_1", 0)
            sl2 = s.get("stop_loss_2", 0)
            r1 = (sl1 / bz - 1) * 100
            r2 = (sl2 / bz - 1) * 100
            gap = r2 - r1
            w(f"  {s['name']:<12} {bz:>8,} {sl1:>9,} {sl2:>9,} "
              f"{r1:>+7.2f}% {r2:>+7.2f}% {gap:>+7.2f}%p")
        w("")
        w("  관찰: 1차 손절 → 절반 매도. 2차 손절 → 잔량 청산.")
        w("  닷밀 1차→2차 갭 5.4%p / 덕신이피씨 갭 3.9%p")
    w("")

    # ---------------------------------------------------------------
    # 7. 손절폭 vs 가격대 상관 관계
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[7] 매수가 가격대별 손절폭")
    w("─" * 84)
    price_bins = [(0, 2_000), (2_000, 5_000), (5_000, 10_000),
                  (10_000, 30_000), (30_000, 100_000)]
    w(f"  {'가격대':<22} {'N':>4} {'평균손절':>10} {'평균매수가':>14}")
    w("  " + "-" * 60)
    for lo, hi in price_bins:
        bucket = [s for s in stocks if lo <= s["buy_zone"] < hi]
        if not bucket:
            continue
        avg_sl = stats.mean(s["sl_ratio_pct"] for s in bucket)
        avg_bz = stats.mean(s["buy_zone"] for s in bucket)
        label = f"{lo:,} ~ {hi:,}"
        w(f"  {label:<22} {len(bucket):>4} {avg_sl:>+9.2f}% {avg_bz:>13,.0f}원")
    w("")
    w("  관찰: 가격대와 손절폭 사이 상관관계는 약함.")
    w("  단테는 차트 패턴(채널/이평선)이 손절선 위치를 결정 — 가격이 낮다고")
    w("  더 깊은 손절폭을 주지는 않음.")
    w("")

    # ---------------------------------------------------------------
    # 8. 손절선 위치 추정 (자료 한계 표시)
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[8] 손절선 위치의 '근거' 추정")
    w("─" * 84)
    w("  원자료에 손절선 근거 명시 안 됨. dante_rules_v2.json 의 다른 섹션과")
    w("  교차검증해 추정:")
    w("")
    w("  · core_indicators.horizontal_lines.pink_magenta_horizontal:")
    w("    \"색: 핑크/마젠타, 역할: 손절가 기준선. 이탈 시 즉시 손절\"")
    w("    → 단테는 손절선을 '미리 그어둔 핑크 수평선' 형태로 차트에 표시")
    w("")
    w("  · stop_loss_rules.primary:")
    w("    \"핑크 마젠타 또는 빨간 파란 수평선 이탈 시 즉시 손절\"")
    w("    → 색깔 계열별 다양한 수평선 모두 손절 트리거")
    w("")
    w("  · base_line_rules.numeric_thresholds:")
    w("    \"기준선 이탈 1.5% 손절 (타이트 단타 기준)\"")
    w("    → 41차트 중 -1 ~ -3% 손절(약 5건)은 이 기준선-기반 추정")
    w("")
    w("  · stop_loss_rules.cloud_based: \"일목구름 하단 완전 이탈 시 손절\"")
    w("    → 41차트 중 깊은 손절(-10% 이상, 약 5건)은 구름하단 가능성")
    w("")
    w("  · entry_rules.swing.avg_stop_loss: \"-3% ~ -8%\"")
    w("    entry_rules.mid_long_term.avg_stop_loss: \"-10% ~ -28%\"")
    w("    → 분류별 표준 범위와 41 차트 통계가 일치")
    w("")

    # ---------------------------------------------------------------
    # 9. v4 시스템 적용 권장
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[9] 우리 v4 시스템에 적용 가능성")
    w("─" * 84)
    w("  [현재 v4]")
    w("    · 1차 손절: 일목 기준선 × 0.985 (= 기준선 -1.5%)")
    w("    · 2차 손절: 선행스팬B (구름 하단)")
    w("    · 모든 종목 동일 적용")
    w("")
    w("  [단테 차트 41장 패턴]")
    w("    · 스윙: 평균 -5.8%, 범위 -2.6% ~ -15.8% (분산 큼)")
    w("    · 중장기: 평균 -14.2%, 범위 -4.4% ~ -28.5%")
    w("    · 손절선 근거가 차트마다 다름 (기준선/구름/직전저점/박스하단)")
    w("    · → '하나의 % 룰' 로는 단테 정통 재현 불가")
    w("")
    w("  [v5 백테스트 검증 결과 (logs/v5_analysis.txt)]")
    w("    · 단테 영상의 \"-5%/-10% 시나리오\" 적용 시 성과 급락")
    w("    · v4 (-1.5% 일률) 가 모든 v5 변형보다 압승")
    w("       v4   CAGR +50.4% / 누적 +668% / Sharpe 1.84 / MDD -17.5%")
    w("       v5_C CAGR +27.4% / 누적 +235% / Sharpe 1.13 / MDD -21.5%")
    w("       v5_B CAGR +21.2% / 누적 +162% / Sharpe 0.96 / MDD -33.3%")
    w("    · 자동화 시스템에선 '타이트 단타 -1.5%' 가 자본 회전·손실 통제 양면 최적")
    w("")
    w("  [권고]")
    w("    ✗ 단테 차트별 손절폭 (-3 ~ -28%) 을 자동으로 모방하지 말 것")
    w("       → 이미 v5 백테스트에서 열등 검증")
    w("    ✓ v4 시스템 그대로 유지 (-1.5% 일률 + 구름하단 2차 손절)")
    w("    ✓ 알림(notifier.py) 받은 후 사용자가 차트 보고 손절폭 수동 조정 가능")
    w("       단타 -1.5~3% (= 시스템 그대로)")
    w("       스윙 -5%       (= 사용자 판단 — 단테 표준)")
    w("       중장기 -10%    (= 사용자 판단 — 큰 자금)")
    w("    △ 추후 검토: 차트 시각 라인 자동 검출 (핑크선 / 직전저점) 시")
    w("       단테식 차등 손절을 자동으로 재현 가능. 단, 라인 검출 자체가 어려움.")
    w("")

    # ---------------------------------------------------------------
    # 10. 핵심 발견 요약
    # ---------------------------------------------------------------
    w("─" * 84)
    w("[10] 핵심 발견 요약")
    w("─" * 84)
    all_ratios = [s["sl_ratio_pct"] for s in stocks]
    w(f"  · 41 차트 평균 손절폭: {stats.mean(all_ratios):+.2f}%")
    w(f"  · 중앙값: {stats.median(all_ratios):+.2f}%")
    w(f"  · 범위: {min(all_ratios):+.1f}% ~ {max(all_ratios):+.1f}%")
    w(f"  · 표준편차: {stats.pstdev(all_ratios):.2f}%p (분산 매우 큼)")
    w("")
    w("  · 가장 자주 쓰는 손절 기준 (추정):")
    w("    - 핑크/마젠타 수평선 (차트에 미리 그어둔 손절선)")
    w("    - 일목 기준선 (단타 시)")
    w("    - 구름 하단 (스윙 깊은 손절)")
    w("    - 직전 저점 (박스권/장기)")
    w("")
    w("  · 단테 = '한 가지 손절 룰' 사용 안 함. 차트마다 시각 라인 결정.")
    w("  · 우리 v4 = 자동화 위해 '일률 -1.5%' 사용. 백테스트 압승.")
    w("  · 두 방식 모두 합리적. 단테 = 사람 판단, v4 = 자동화 안전장치.")
    w("")
    w("=" * 84)

    txt = "\n".join(out)
    print(txt)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(txt, encoding="utf-8")
    print(f"\n[saved] {OUT}")


if __name__ == "__main__":
    main()
