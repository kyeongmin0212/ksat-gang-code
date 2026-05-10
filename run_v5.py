"""v5 전략 실행 드라이버 (v2 + 단테 스윙 재현)

v5 추가사항:
- A. 스윙_중장기 목표: 3목표 중 최소값 선택 (빠른 익절)
- B. 2차 손절 제거 + 1차 손절 전량 매도
- C. 트레일링 스톱 (+5% 활성화, 최고점 대비 -2%)
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt  # noqa

BASE = Path(r"C:\Users\sji48\ksat_gang")


def main() -> int:
    # v5 config
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False
    # v5 전용
    bt.CONFIG["use_min_target_for_swing_mid"] = True
    bt.CONFIG["disable_sl2"] = True
    bt.CONFIG["sl1_full_exit"] = True
    bt.CONFIG["enable_trailing_stop"] = True
    bt.CONFIG["trailing_activation_pct"] = 5.0
    bt.CONFIG["trailing_drawdown_pct"] = 2.0

    bt.OUT_PATH = BASE / "backtest_results_v5.json"
    bt.log.info("=" * 70)
    bt.log.info("v5 실행: v2 + 스윙 중장기 최소목표 + SL2 제거 + 트레일링 스톱")
    bt.log.info("=" * 70)
    return bt.run()


if __name__ == "__main__":
    sys.exit(main())
