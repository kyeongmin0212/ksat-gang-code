"""v2 + 우선주 제외 필터 실행.

v2 조건 유지 + 종목명 끝 '우' 또는 '우B' 종목 제외.
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt  # noqa

BASE = Path(r"C:\Users\sji48\ksat_gang")


def main() -> int:
    # v2 순수 설정
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False

    # v5/v6 플래그 off
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False
    bt.CONFIG["enable_trailing_stop"] = False
    bt.CONFIG["target_strategy"] = "median"
    bt.CONFIG["simple_stop_loss_pct"] = None

    # 추가 필터 ⭐
    bt.CONFIG["exclude_preferred_stocks"] = True

    bt.OUT_PATH = BASE / "backtest_results_v2_no_pref.json"
    bt.log.info("=" * 70)
    bt.log.info("v2 + 우선주 제외 실행")
    bt.log.info("=" * 70)
    return bt.run()


if __name__ == "__main__":
    sys.exit(main())
