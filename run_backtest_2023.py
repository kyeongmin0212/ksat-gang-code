"""2023-01-01 ~ 2026-04-22 정상 시장 구간에서 v1/v2/v4 3종 연속 백테스팅.

데이터 로딩·rolling 계산은 한 번만 하고 3번 재사용.
각 버전마다 compute_signals 에 다른 config 적용 → simulate → 저장.
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import json
import gc
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt  # noqa


BASE_DIR = Path(r"C:\Users\sji48\ksat_gang")
START = "20230101"
END = "20260422"

# 버전별 config override
VERSIONS = {
    "2023_v1": {
        "target_scores": [3, 4, 5, 6, 7, 8],
        "enable_bear_market_filter": False,
        "require_above_ma224": False,
    },
    "2023_v2": {
        "target_scores": [4],
        "enable_bear_market_filter": True,
        "require_above_ma224": False,
    },
    "2023_v4": {
        "target_scores": [4],
        "enable_bear_market_filter": False,
        "require_above_ma224": True,
    },
}


def main() -> int:
    bt.log.info("=" * 70)
    bt.log.info("정상시장 백테스팅 (2023~) 3종 연속 실행")
    bt.log.info("=" * 70)

    # 공통: 날짜 범위 고정
    bt.CONFIG["start_date"] = START
    bt.CONFIG["end_date"] = END

    # --- 데이터 로딩/rolling (1회) ---
    t0 = time.time()
    df_base = bt.load_merged_data(START, END)
    df_base = bt.compute_rolling_stats(df_base, bt.CONFIG)
    bt.log.info(f"데이터 준비 완료: {time.time()-t0:.1f}s / rows={len(df_base):,}")

    # --- KOSPI regime (v2 전용) ---
    kospi_regime: dict[str, bool] | None = None

    # --- 버전별 실행 ---
    for label, overrides in VERSIONS.items():
        bt.log.info("─" * 60)
        bt.log.info(f"▶ 실행: {label}")
        bt.log.info(f"  overrides: {overrides}")

        # CONFIG 덮어쓰기
        for k, v in overrides.items():
            bt.CONFIG[k] = v

        # signal 재계산 (df_base 에 in-place 로 signal 열만 덮어씀)
        df = bt.compute_signals(df_base, bt.CONFIG)

        # KOSPI regime — 필요할 때만 로드
        if bt.CONFIG["enable_bear_market_filter"]:
            if kospi_regime is None:
                kospi_regime = bt.load_kospi_regime(START, END, bt.CONFIG)
            active_regime = kospi_regime
        else:
            active_regime = {}

        # 시뮬레이션
        trades, pv_history, sim_meta = bt.simulate(df, bt.CONFIG, active_regime)

        # 통계
        stats = bt.compute_statistics(trades, pv_history, bt.CONFIG)

        # 저장
        out_path = BASE_DIR / f"backtest_results_{label}.json"
        payload = {
            "meta": {
                "version": label,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "period_start": START,
                "period_end": END,
                "config_snapshot": dict(bt.CONFIG),
                "simulation_meta": sim_meta,
                "limitations": [
                    "현재 상장 종목만 대상 (생존 편향)",
                    "동일 종목 중복 보유 금지",
                    "호가 단위 스냅 후 체결 가정",
                    "정상시장 구간 (2023~) 평가",
                ],
            },
            "statistics": stats,
        }
        bt.save_json_atomic(payload, out_path)

        ov = stats.get("overall", {})
        bt.log.info(
            f"✓ {label}: 거래 {ov.get('total_trades',0)} "
            f"승률 {ov.get('win_rate_pct',0):.2f}% "
            f"누적 {ov.get('cumulative_return_pct',0):.2f}% "
            f"CAGR {ov.get('cagr_pct',0):.2f}% "
            f"MDD {ov.get('mdd_pct',0):.2f}% "
            f"Sharpe {ov.get('sharpe',0):.2f} "
            f"→ {out_path.name}"
        )

        # 메모리 정리 (신호 컬럼만 제거)
        for col in ("signal",):
            if col in df_base.columns:
                del df_base[col]
        gc.collect()

    bt.log.info("=" * 70)
    bt.log.info("전체 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
