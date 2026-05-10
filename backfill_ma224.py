"""ma224 / ma448 backfill.

dante_strategy.py 의 daily 모드가 300일 lookback만 써서 ma224(약 320 캘린더일 필요)
가 최근 데이터에서 NULL 로 덮어쓰임. 직접 OHLCV 전체 시계열로 ma224/ma448 재계산.
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
sys.stdout.reconfigure(encoding="utf-8")

import sqlite3
from pathlib import Path
import pandas as pd
import numpy as np

DB = Path(r"C:\Users\sji48\ksat_gang\stock_data.db")


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-400000")

    tickers = [r[0] for r in conn.execute("SELECT DISTINCT 종목코드 FROM ticker_master").fetchall()]
    print(f"종목 {len(tickers):,}건 ma224/ma448 backfill")
    n_total = 0
    n_done = 0
    for i, t in enumerate(tickers, 1):
        df = pd.read_sql_query(
            "SELECT 날짜, 종가 FROM daily_data WHERE 종목코드=? ORDER BY 날짜 ASC",
            conn, params=(t,),
        )
        if len(df) < 224:
            n_done += 1
            continue
        ma224 = df["종가"].rolling(224, min_periods=224).mean()
        ma448 = df["종가"].rolling(448, min_periods=448).mean()
        df["ma224"] = ma224
        df["ma448"] = ma448
        # NULL이 아닌 행만 update
        df = df.dropna(subset=["ma224"], how="all")
        rows = [
            (
                None if pd.isna(r["ma224"]) else float(r["ma224"]),
                None if pd.isna(r["ma448"]) else float(r["ma448"]),
                int(r["날짜"]), t,
            )
            for _, r in df.iterrows()
        ]
        if rows:
            conn.executemany(
                "UPDATE daily_indicators SET ma224=?, ma448=? WHERE 날짜=? AND 종목코드=?",
                rows,
            )
            n_total += len(rows)
        n_done += 1
        if i % 500 == 0:
            conn.commit()
            print(f"  {i}/{len(tickers)} ({n_total:,} 행 갱신)")
    conn.commit()
    conn.close()
    print(f"완료 — {n_done}/{len(tickers)} 종목, 총 {n_total:,} 행 갱신")
    return 0


if __name__ == "__main__":
    sys.exit(main())
