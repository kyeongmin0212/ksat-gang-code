"""오늘 후보(candidates_v4.json) 에 v6 필터 적용 → candidates_v6.json.

v6 추가 필터:
  · 종가 > MA224
  · 종가 > MA112
  · 60일 박스권 (wave_high - wave_low) / wave_low ≤ 30%

DB(daily_indicators + daily_data)에서 ref_date 의 ma112/ma224/wave_high/wave_low 조회.
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
sys.stdout.reconfigure(encoding="utf-8")

import json
import sqlite3
from pathlib import Path
from datetime import datetime

BASE   = Path(r"C:\Users\sji48\ksat_gang")
DB     = BASE / "stock_data.db"
IN     = BASE / "candidates_v4.json"
OUT    = BASE / "candidates_v6.json"
LOG    = BASE / "logs" / "v6_filter_today.txt"

WAVE_LOOKBACK = 60
BOX_RANGE_MAX = 0.30


def fetch_v6_indicators(conn: sqlite3.Connection, ticker: str, ref_date: str) -> dict:
    """ref_date 의 ma112, ma224, 그리고 60일 wave_high/wave_low 조회."""
    row = conn.execute(
        "SELECT ma112, ma224 FROM daily_indicators WHERE 종목코드=? AND 날짜=?",
        (ticker, ref_date),
    ).fetchone()
    if not row:
        return {}
    ma112 = float(row[0]) if row[0] is not None else None
    ma224 = float(row[1]) if row[1] is not None else None

    # 60일 wave_high / wave_low — ref_date 기준 직전 60거래일
    wave_rows = conn.execute(
        """SELECT MAX(고가), MIN(저가) FROM (
              SELECT 고가, 저가 FROM daily_data
              WHERE 종목코드=? AND 날짜<=?
              ORDER BY 날짜 DESC LIMIT ?
           )""",
        (ticker, ref_date, WAVE_LOOKBACK),
    ).fetchone()
    wave_high = float(wave_rows[0]) if wave_rows and wave_rows[0] is not None else None
    wave_low  = float(wave_rows[1]) if wave_rows and wave_rows[1] is not None else None
    return {"ma112": ma112, "ma224": ma224, "wave_high": wave_high, "wave_low": wave_low}


def main() -> int:
    if not IN.exists():
        print(f"[ERR] {IN} 없음")
        return 1
    data = json.loads(IN.read_text(encoding="utf-8"))
    ref_date = data["date"]
    cands = data.get("tradable_candidates") or []

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA cache_size=-200000")

    out_cands: list[dict] = []
    rejected: list[dict] = []
    log_rows: list[str] = []

    for c in cands:
        t = c["ticker"]
        close = c["close"]
        v6 = fetch_v6_indicators(conn, t, ref_date)
        ma112 = v6.get("ma112")
        ma224 = v6.get("ma224")
        wh = v6.get("wave_high")
        wl = v6.get("wave_low")
        box_range = ((wh - wl) / wl) if (wh and wl and wl > 0) else None

        # 필터 평가
        f_ma224 = (ma224 is not None) and (close > ma224)
        f_ma112 = (ma112 is not None) and (close > ma112)
        f_box   = (box_range is not None) and (box_range <= BOX_RANGE_MAX)

        all_pass = bool(f_ma224 and f_ma112 and f_box)

        # 이유 컴포지션
        reasons = []
        if not f_ma224:
            r = f"MA224 위반 (종가 {close:,} ≤ MA224 {ma224 or 'N/A'})"
            reasons.append(r)
        if not f_ma112:
            r = f"MA112 위반 (종가 {close:,} ≤ MA112 {ma112 or 'N/A'})"
            reasons.append(r)
        if not f_box:
            r = f"BOX 위반 (60일 변동폭 {box_range*100 if box_range else 0:.1f}% > 30%)"
            reasons.append(r)

        c_v6 = dict(c)
        c_v6["v6_filters"] = {
            "ma112": ma112,
            "ma224": ma224,
            "wave_high_60d": wh,
            "wave_low_60d": wl,
            "box_range_pct": round(box_range * 100, 2) if box_range is not None else None,
            "pass_ma224": f_ma224,
            "pass_ma112": f_ma112,
            "pass_box": f_box,
            "all_pass": all_pass,
        }

        log_rows.append(
            f"  {c['name']:<14} ({t}) close={close:,} "
            f"MA112={ma112 or '?':>8} MA224={ma224 or '?':>8} "
            f"BOX={box_range*100 if box_range is not None else 0:>5.1f}%  "
            f"{'✓' if all_pass else '✗ ' + ' / '.join(reasons)}"
        )

        if all_pass:
            out_cands.append(c_v6)
        else:
            c_v6["v6_reject_reasons"] = reasons
            rejected.append(c_v6)

    conn.close()

    # 출력 JSON
    out_data = dict(data)
    out_data["strategy"] = "v6 (v4 + MA224 + MA112 + 60일 박스권)"
    out_data["v6_config"] = {
        "wave_lookback_days": WAVE_LOOKBACK,
        "box_range_max_pct": BOX_RANGE_MAX * 100,
        "filters_added": ["close > MA224", "close > MA112", "60d box range ≤ 30%"],
    }
    out_data["tradable_candidates"] = out_cands
    out_data["v6_rejected_candidates"] = rejected
    out_data["filter_summary"]["v4_to_v6"] = {
        "v4_count": len(cands),
        "v6_count": len(out_cands),
        "rejected_count": len(rejected),
    }

    OUT.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 로그
    log_text = "\n".join([
        "=" * 88,
        f"  v6 필터 적용 — 오늘({ref_date}) v4 후보 → v6 후보",
        "=" * 88,
        "",
        f"v4 후보: {len(cands)}건",
        f"v6 후보: {len(out_cands)}건",
        f"탈락:     {len(rejected)}건",
        "",
        "[종목별 결과]",
        *log_rows,
        "",
        "=" * 88,
    ])
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(log_text, encoding="utf-8")

    print(log_text)
    print(f"\n[saved] {OUT}")
    print(f"[saved] {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
