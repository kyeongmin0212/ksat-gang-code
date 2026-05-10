"""v6 = v4 + 단테 추가 기법:
   · 224일선 위 진입 (require_above_ma224=True)  — 단테 '부모 라인'
   · 112일선 위 진입 (require_above_ma112=True)  — 중기 추세
   · 60일 박스권 (변동성 ≤ 30%)                   — 채널/밥그릇 자동 검출

진입 조건은 v4 그대로 (점수4 + base_line_near + not_overheated + 중장기 + 우선주
제외 + KOSPI bear 필터 + 30일 쿨다운 + 블랙리스트).

(이전 v6 실험: 단순 -5% 손절 + 전고점 목표 — 본 파일에서 새 정의로 대체됨.)
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
    # v4 기본
    bt.CONFIG["start_date"] = "20210423"
    bt.CONFIG["end_date"]   = "20260422"
    bt.CONFIG["target_scores"] = [4]
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["exclude_preferred_stocks"] = True
    bt.CONFIG["allowed_position_types"] = ["중장기"]
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False
    bt.CONFIG["enable_trailing_stop"] = False
    bt.CONFIG["target_strategy"] = "median"
    bt.CONFIG["simple_stop_loss_pct"] = None
    bt.CONFIG["stop_loss_1_base_deviation_pct"] = -0.015
    bt.CONFIG["stage_amounts"] = [100_000, 200_000, 400_000, 300_000]

    # v4 진입 강화
    bt.CONFIG["require_base_line_near"] = True
    bt.CONFIG["require_not_overheated_entry"] = True
    bt.CONFIG["re_entry_cooldown_days"] = 30
    bt.CONFIG["blacklist_enabled"] = True
    bt.CONFIG["blacklist_lookback"] = 5
    bt.CONFIG["blacklist_threshold"] = 3
    bt.CONFIG["blacklist_ban_days"] = 252

    # v6 추가 단테 기법
    bt.CONFIG["version"] = "v6"
    bt.CONFIG["require_above_ma224"] = True   # 224일선 (부모 라인)
    bt.CONFIG["require_above_ma112"] = True   # 112일선 (중기 추세)
    bt.CONFIG["require_box_pattern"] = True   # 60일 박스권 (변동성 ≤ 30%)
    bt.CONFIG["box_range_max"] = 0.30

    bt.OUT_PATH = BASE / "backtest_results_v6.json"
    bt.log.info("=" * 70)
    bt.log.info("v6 실행 — v4 + MA224 + MA112 + 60일 박스권(≤30%)")
    bt.log.info("=" * 70)
    return bt.run()


if __name__ == "__main__":
    sys.exit(main())
