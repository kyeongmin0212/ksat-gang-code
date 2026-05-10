"""candidates_YYYY-MM-DD.json -> candidates_YYYY-MM-DD.md
Tradable candidates only, sorted by expected return % desc.
Strictly limited fields (no buy_stages, no indicators_snapshot).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

CONDITION_KO: dict[str, str] = {
    "cloud_above_std": "구름대 위",
    "cloud_above_2x": "구름대 2배 위",
    "base_line_near": "기준선 근처",
    "ma_convergence": "이평선 수렴",
    "volume_surge": "거래량 급증",
    "accumulation_bar": "매집봉",
    "bb_lower_touch": "볼린저 하단 터치",
    "base_line_not_overheated": "과열 아님",
}

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def format_date_header(date_raw: str) -> tuple[str, str]:
    """Accepts 'YYYYMMDD' or 'YYYY-MM-DD'. Returns (iso, weekday_ko)."""
    s = str(date_raw)
    if len(s) == 8 and s.isdigit():
        d = datetime.strptime(s, "%Y%m%d")
    else:
        d = datetime.strptime(s, "%Y-%m-%d")
    return d.strftime("%Y-%m-%d"), WEEKDAY_KO[d.weekday()]


def render_md(payload: dict) -> str:
    iso, weekday = format_date_header(payload.get("date", ""))
    is_bull = payload.get("market_state", {}).get("is_bull", True)
    market_label = "🌞 강세장" if is_bull else "🐻 약세장"
    tradable = payload.get("tradable_candidates", []) or []

    enriched: list[tuple[float, dict]] = []
    for c in tradable:
        close = c.get("close") or 0
        target = c.get("target_median") or 0
        ret_pct = ((target - close) / close * 100.0) if close else 0.0
        enriched.append((ret_pct, c))
    enriched.sort(key=lambda x: x[0], reverse=True)

    lines: list[str] = []
    lines.append(f"# {iso} ({weekday}) 매수 추천")
    lines.append("")
    lines.append(f"{market_label} — {len(tradable)}종목")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, (ret_pct, c) in enumerate(enriched, start=1):
        name = c.get("name", "")
        ticker = c.get("ticker", "")
        close = c.get("close") or 0
        sl1 = c.get("sl1") or 0
        target = c.get("target_median") or 0
        position_type = c.get("position_type", "")
        conditions = c.get("conditions", {}) or {}
        reasons = [CONDITION_KO[k] for k, v in conditions.items() if v and k in CONDITION_KO]
        reasons_text = ", ".join(reasons) if reasons else "-"

        lines.append(f"## {i}. {name} ({ticker})")
        lines.append(f"- 💰 현재가: {close:,}원")
        lines.append(f"- 🛑 손절가: {sl1:,}원")
        lines.append(f"- 🎯 목표가: {target:,}원 ({ret_pct:+.0f}%)")
        lines.append(f"- 📊 분류: {position_type}")
        lines.append(f"- 💡 매수 이유: {reasons_text}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def find_history_jsons(history_dir: Path) -> list[Path]:
    files = sorted(history_dir.glob("candidates_*.json"))
    return [f for f in files if ".bak" not in f.name and ".tmp" not in f.name]


def convert_file(json_path: Path) -> Path:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    md_path = json_path.with_suffix(".md")
    md_path.write_text(render_md(payload), encoding="utf-8")
    return md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", action="store_true", help="최신 1개만 변환")
    parser.add_argument("--history-dir", default=None)
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    history_dir = Path(args.history_dir) if args.history_dir else (base / "history")

    files = find_history_jsons(history_dir)
    if not files:
        print("no candidates_*.json found")
        return 1

    if args.latest:
        files = [files[-1]]

    for f in files:
        out = convert_file(f)
        print(f"  {f.name} -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
