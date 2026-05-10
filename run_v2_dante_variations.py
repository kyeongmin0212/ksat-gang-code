"""v2_dante 최적화 실험 — 5종 연속 백테스팅.

- v2_original        : 점수4 + KOSPI200MA + 우선주 포함 (baseline)
- v2_dante           : v2_original + 우선주 제외
- v2_dante_A         : v2_dante + 중장기만 (스윙_중장기 제외)
- v2_dante_B         : v2_dante + 점수 4~5점
- v2_dante_C         : v2_dante + 목표가 최대값

전부 동일 기간 + 자본/수수료 조건에서 비교.
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

BASE = Path(r"C:\Users\sji48\ksat_gang")
OUT_JSON = BASE / "backtest_results_v2_dante_variations.json"
OUT_TXT = BASE / "logs" / "v2_dante_compare.txt"

START = "20210423"
END = "20260422"

# 각 버전의 override
VERSIONS: list[tuple[str, dict, str]] = [
    ("v2_original", {
        "exclude_preferred_stocks": False,
        "target_scores": [4],
        "target_strategy": "median",
        "allowed_position_types": None,
    }, "원본 — 우선주 포함"),
    ("v2_dante", {
        "exclude_preferred_stocks": True,
        "target_scores": [4],
        "target_strategy": "median",
        "allowed_position_types": None,
    }, "원본 + 우선주 제외"),
    ("v2_dante_A", {
        "exclude_preferred_stocks": True,
        "target_scores": [4],
        "target_strategy": "median",
        "allowed_position_types": ["중장기"],
    }, "v2_dante + 중장기만"),
    ("v2_dante_B", {
        "exclude_preferred_stocks": True,
        "target_scores": [4, 5],
        "target_strategy": "median",
        "allowed_position_types": None,
    }, "v2_dante + 점수 4~5"),
    ("v2_dante_C", {
        "exclude_preferred_stocks": True,
        "target_scores": [4],
        "target_strategy": "max",
        "allowed_position_types": None,
    }, "v2_dante + 목표 최대값"),
]


def apply_base_config() -> None:
    """모든 v5/v6 플래그 OFF + 공통 v2 설정."""
    bt.CONFIG["start_date"] = START
    bt.CONFIG["end_date"] = END
    bt.CONFIG["enable_bear_market_filter"] = True
    bt.CONFIG["require_above_ma224"] = False
    # v5 flags
    bt.CONFIG["use_min_target_for_swing_mid"] = False
    bt.CONFIG["disable_sl2"] = False
    bt.CONFIG["sl1_full_exit"] = False
    bt.CONFIG["enable_trailing_stop"] = False
    # v6 flags
    bt.CONFIG["simple_stop_loss_pct"] = None


def main() -> int:
    t_all = time.time()
    bt.log.info("=" * 70)
    bt.log.info("v2_dante 최적화 실험 (5종)")
    bt.log.info("=" * 70)

    # 데이터 로딩은 우선주 포함/제외 2번만
    apply_base_config()

    # KOSPI regime — 한 번만
    kospi_regime = bt.load_kospi_regime(START, END, bt.CONFIG)

    bt.CONFIG["exclude_preferred_stocks"] = False
    df_with_pref = bt.load_merged_data(START, END, bt.CONFIG)
    df_with_pref = bt.compute_rolling_stats(df_with_pref, bt.CONFIG)

    bt.CONFIG["exclude_preferred_stocks"] = True
    df_no_pref = bt.load_merged_data(START, END, bt.CONFIG)
    df_no_pref = bt.compute_rolling_stats(df_no_pref, bt.CONFIG)

    results: dict[str, dict] = {}
    for version, overrides, desc in VERSIONS:
        bt.log.info("─" * 60)
        bt.log.info(f"▶ {version}  ({desc})")

        apply_base_config()
        for k, v in overrides.items():
            bt.CONFIG[k] = v

        df = df_no_pref if bt.CONFIG["exclude_preferred_stocks"] else df_with_pref
        df = bt.compute_signals(df, bt.CONFIG)

        trades, pv_history, sim_meta = bt.simulate(df, bt.CONFIG, kospi_regime)
        stats = bt.compute_statistics(trades, pv_history, bt.CONFIG)

        results[version] = {
            "desc": desc,
            "config": {
                "exclude_preferred_stocks": bt.CONFIG["exclude_preferred_stocks"],
                "target_scores": bt.CONFIG["target_scores"],
                "target_strategy": bt.CONFIG["target_strategy"],
                "allowed_position_types": bt.CONFIG["allowed_position_types"],
            },
            "simulation_meta": sim_meta,
            "statistics": stats,
        }

        ov = stats.get("overall", {})
        bt.log.info(
            f"  거래 {ov.get('total_trades',0):,} "
            f"승률 {ov.get('win_rate_pct',0):.2f}% "
            f"누적 {ov.get('cumulative_return_pct',0):+.2f}% "
            f"CAGR {ov.get('cagr_pct',0):+.2f}% "
            f"MDD {ov.get('mdd_pct',0):.2f}% "
            f"Sharpe {ov.get('sharpe',0):.2f}"
        )
        gc.collect()

    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "period": f"{START} ~ {END}",
            "experiment_count": len(VERSIONS),
        },
        "versions": results,
    }
    bt.save_json_atomic(payload, OUT_JSON)

    # 비교 텍스트 리포트
    lines = []
    lines.append("=" * 95)
    lines.append("  v2_dante 최적화 실험 비교 (5종, 기간: " + START + " ~ " + END + ")")
    lines.append("=" * 95)
    lines.append("")
    lines.append(f'{"버전":<14} {"설명":<24} {"거래":>7} {"승률":>8} {"평균수익":>10} {"누적":>10} {"CAGR":>9} {"MDD":>9} {"Sharpe":>8} {"최종자산":>14}')
    lines.append("-" * 130)
    for version, _ov, desc in VERSIONS:
        r = results[version]
        o = r["statistics"]["overall"]
        lines.append(
            f'{version:<14} {desc:<24} {o.get("total_trades",0):>7,} '
            f'{o.get("win_rate_pct",0):>7.2f}% {o.get("avg_return_pct",0):>+9.2f}% '
            f'{o.get("cumulative_return_pct",0):>+9.2f}% {o.get("cagr_pct",0):>+8.2f}% '
            f'{o.get("mdd_pct",0):>8.2f}% {o.get("sharpe",0):>8.2f} '
            f'{o.get("final_portfolio_value",0):>14,.0f}'
        )

    # 연도별 비교
    lines.append("")
    lines.append("=== 연도별 누적 P&L (원) ===")
    all_years = set()
    for r in results.values():
        all_years.update(r["statistics"]["by_year"].keys())
    years = sorted(all_years)
    header = f'{"연도":<6} | ' + " | ".join(f'{v:>14}' for v, _, _ in VERSIONS)
    lines.append(header)
    lines.append("-" * len(header))
    for y in years:
        cells = []
        for version, _, _ in VERSIONS:
            r = results[version]["statistics"]["by_year"].get(y, {})
            cells.append(f'{r.get("total_pnl_krw", 0):>14,.0f}')
        lines.append(f'{y:<6} | ' + " | ".join(cells))

    # 포지션 타입별
    lines.append("")
    lines.append("=== 포지션 타입별 누적 P&L (원) ===")
    lines.append(f'{"타입":<14} | ' + " | ".join(f'{v:>14}' for v, _, _ in VERSIONS))
    lines.append("-" * 130)
    for pt in ("스윙_중장기", "중장기"):
        cells = []
        for version, _, _ in VERSIONS:
            r = results[version]["statistics"]["by_position_type"].get(pt, {})
            cells.append(f'{r.get("total_pnl_krw", 0):>14,.0f}')
        lines.append(f'{pt:<14} | ' + " | ".join(cells))

    OUT_TXT.write_text("\n".join(lines), encoding="utf-8")
    bt.log.info(f"저장: {OUT_JSON.name} + {OUT_TXT.name} / 총 {time.time()-t_all:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
