"""v5 = 단테 정통 손절·분할매수 방식 검증.

3개 변형:
  v5_A: 1차 손절 -10% (기준선 기준), 1:2:4:3, 1차 절반매도
  v5_B: 1차 손절 -10%,                1:2:4:8, 1차 절반매도
  v5_C: 1차 손절  -5%,                1:2:4:3, 1차 절반매도

진입 조건은 v4 그대로 (점수4 + 기준선근접 + 비과열 + 중장기 + 우선주 제외 +
재진입 쿨다운 30일 + 블랙리스트 + KOSPI bear 필터).

1차 절반매도는 backtesting.py 디폴트 동작 (sl1_full_exit=False).
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import gc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt

BASE = Path(r"C:\Users\sji48\ksat_gang")


def apply_v4_base() -> None:
    """v4 공통 진입 조건. run_v4_improved.py 와 동일."""
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"]   = "20260422"
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False
    bt.CONFIG["exclude_preferred_stocks"] = True
    bt.CONFIG["allowed_position_types"] = ["중장기"]
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False              # ← 1차 손절 시 절반 매도 (단테 정통)
    bt.CONFIG["enable_trailing_stop"] = False
    bt.CONFIG["target_strategy"] = "median"
    bt.CONFIG["simple_stop_loss_pct"] = None
    # v4 개선
    bt.CONFIG["require_base_line_near"] = True
    bt.CONFIG["require_not_overheated_entry"] = True
    bt.CONFIG["re_entry_cooldown_days"] = 30
    bt.CONFIG["blacklist_enabled"] = True
    bt.CONFIG["blacklist_lookback"] = 5
    bt.CONFIG["blacklist_threshold"] = 3
    bt.CONFIG["blacklist_ban_days"] = 252


def run_variant(name: str, sl1_pct: float, stages: list[int], desc: str) -> None:
    apply_v4_base()
    bt.CONFIG["version"] = name
    bt.CONFIG["stop_loss_1_base_deviation_pct"] = sl1_pct
    bt.CONFIG["stage_amounts"] = stages
    out = BASE / f"backtest_results_{name}.json"
    bt.OUT_PATH = out
    bt.log.info("=" * 70)
    bt.log.info(f"{name} 실행 — {desc}")
    bt.log.info(f"  1차 손절: 기준선 × (1 + {sl1_pct})  (= {sl1_pct*100:+.1f}%)")
    bt.log.info(f"  분할매수: {stages} (sum={sum(stages):,})")
    bt.log.info(f"  1차 손절 시 절반 매도 (sl1_full_exit=False)")
    bt.log.info("=" * 70)
    bt.run()
    gc.collect()


def main() -> int:
    # v4 baseline 재실행 (close_reason 통계 보강 후 비교 가능 데이터 확보)
    run_variant(
        name="v4",
        sl1_pct=-0.015,
        stages=[100_000, 200_000, 400_000, 300_000],
        desc="baseline — 1차 -1.5%(기준선), 1:2:4:3, 절반매도",
    )

    # v5_A: 1차 -10%, 1:2:4:3 (= 100/200/400/300k)
    run_variant(
        name="v5_A",
        sl1_pct=-0.10,
        stages=[100_000, 200_000, 400_000, 300_000],
        desc="1차 -10%, 1:2:4:3 (현 v4 stage_amounts), 절반매도",
    )

    # v5_B: 1차 -10%, 1:2:4:8 (= 66.6/133.3/266.7/533.3k, sum=1M)
    run_variant(
        name="v5_B",
        sl1_pct=-0.10,
        stages=[66_667, 133_333, 266_667, 533_333],
        desc="1차 -10%, 1:2:4:8 (단테 원본 비중), 절반매도",
    )

    # v5_C: 1차 -5%, 1:2:4:3
    run_variant(
        name="v5_C",
        sl1_pct=-0.05,
        stages=[100_000, 200_000, 400_000, 300_000],
        desc="1차 -5% (스윙 시나리오), 1:2:4:3, 절반매도",
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
