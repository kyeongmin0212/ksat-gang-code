"""v4 = v2_dante_A + 손실 분석 기반 3대 개선.

- 진입 조건 강화: score==4 AND base_line_near AND base_line_not_overheated
- 재매수 쿨다운: 매도 후 30 영업일 재진입 금지
- 블랙리스트: 최근 5거래 중 3회 손절 시 1년(252영업일) 금지
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtesting as bt

BASE = Path(r"C:\Users\sji48\ksat_gang")


def main() -> int:
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"] = "20260422"
    # v2_dante_A 기본
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

    # v4 개선사항
    bt.CONFIG["require_base_line_near"] = True
    bt.CONFIG["require_not_overheated_entry"] = True
    bt.CONFIG["re_entry_cooldown_days"] = 30
    bt.CONFIG["blacklist_enabled"] = True
    bt.CONFIG["blacklist_lookback"] = 5
    bt.CONFIG["blacklist_threshold"] = 3
    bt.CONFIG["blacklist_ban_days"] = 252

    bt.OUT_PATH = BASE / "backtest_results_v4.json"
    bt.log.info("=" * 70)
    bt.log.info("v4 실행 — 진입조건 강화 + 쿨다운 + 블랙리스트")
    bt.log.info("=" * 70)
    return bt.run()


if __name__ == "__main__":
    sys.exit(main())
