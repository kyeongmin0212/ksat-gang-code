"""Generate history/README.md from existing candidates_*.json files.
Latest day full body + chronological links to all past days.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from json_to_md import find_history_jsons, format_date_header, render_md  # noqa: E402


def main() -> int:
    base = Path(__file__).resolve().parent.parent
    history_dir = base / "history"

    files = find_history_jsons(history_dir)
    if not files:
        print("no candidates files")
        return 1

    latest = files[-1]
    latest_payload = json.loads(latest.read_text(encoding="utf-8"))
    iso, weekday = format_date_header(latest_payload.get("date", ""))
    is_bull = latest_payload.get("market_state", {}).get("is_bull", True)
    emoji = "🌞 강세장" if is_bull else "🐻 약세장"
    n = len(latest_payload.get("tradable_candidates", []) or [])

    # Extract body (skip the date heading + market line + first "---")
    latest_md_lines = render_md(latest_payload).splitlines()
    body_start = 0
    for i, line in enumerate(latest_md_lines):
        if line.strip() == "---":
            body_start = i + 1
            break
    body = "\n".join(latest_md_lines[body_start:]).strip()

    past_links: list[str] = []
    for f in reversed(files):
        p = json.loads(f.read_text(encoding="utf-8"))
        f_iso, f_wd = format_date_header(p.get("date", ""))
        f_n = len(p.get("tradable_candidates", []) or [])
        past_links.append(f"- [{f_iso} ({f_wd}) — {f_n}종목]({f.with_suffix('.md').name})")

    out: list[str] = []
    out.append(f"📅 최신: {iso} ({weekday})")
    out.append(f"{emoji} — {n}종목")
    out.append("")
    out.append("> 매일 평일 19:00 자동 분석 후 GitHub에 자동 업로드됩니다.")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## 🔥 오늘의 매수 추천")
    out.append("")
    out.append(body)
    out.append("")
    out.append("---")
    out.append("")
    out.append("## 📅 과거 추천 보기")
    out.append("")
    out.extend(past_links)
    out.append("")

    (history_dir / "README.md").write_text("\n".join(out), encoding="utf-8")
    print(f"README.md generated (latest={latest.name}, total {len(past_links)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
