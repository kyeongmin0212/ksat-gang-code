"""대시보드 공용 유틸 — 데이터 로드 + 성과 계산.

데이터 소스:
  · history/candidates_YYYY-MM-DD.json (날짜별 추천 백업)
  · candidates_v4.json (오늘 — fallback)
  · stock_data.db (OHLCV + 인디케이터)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

BASE = Path(r"C:\Users\sji48\ksat_gang")
DB   = BASE / "stock_data.db"
HISTORY_DIR  = BASE / "history"
TODAY_PATH   = BASE / "candidates_v4.json"


# ============================================================
# DB 연결 (Streamlit 캐시용)
# ============================================================
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB), check_same_thread=False)
    conn.execute("PRAGMA cache_size=-200000")
    return conn


# ============================================================
# 추천 데이터 로딩
# ============================================================
def list_history_dates() -> list[str]:
    """history/ 안의 추천 데이터 날짜 리스트 (내림차순, YYYY-MM-DD 형식)."""
    if not HISTORY_DIR.exists():
        return []
    dates = []
    for p in HISTORY_DIR.glob("candidates_*.json"):
        stem = p.stem.replace("candidates_", "")
        # YYYY-MM-DD 패턴만 수용
        if len(stem) == 10 and stem[4] == "-" and stem[7] == "-":
            dates.append(stem)
    return sorted(dates, reverse=True)


def load_candidates(date_str: Optional[str] = None) -> dict:
    """date_str=None 이면 candidates_v4.json (오늘), 아니면 history 에서 로드."""
    if date_str is None:
        path = TODAY_PATH
    else:
        path = HISTORY_DIR / f"candidates_{date_str}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def candidates_to_df(data: dict) -> pd.DataFrame:
    """tradable_candidates 를 DataFrame 으로 변환 (대시보드 표시용)."""
    rows = data.get("tradable_candidates") or []
    if not rows:
        return pd.DataFrame()
    out = []
    for c in rows:
        bs = c.get("buy_stages") or {}
        prices = [s.get("price", 0) for s in bs.values() if s.get("price")]
        recommended_pct = c.get("recommended_pct", 0)

        # 단테 41장 분류 (notifier.py 와 동일 로직)
        if recommended_pct < 30:
            classification = "스윙"
            sl_pct = -6.24
        elif recommended_pct < 50:
            classification = "스윙&중장기"
            sl_pct = -8.92
        else:
            classification = "중장기"
            sl_pct = -12.97

        close = c.get("close", 0)
        sl_dante = round(close * (1 + sl_pct / 100))

        # RR 재계산 (단테 손절 기준)
        risk = max(close - sl_dante, 0)
        target = c.get("target_median", 0)
        rr = round((target - close) / risk, 2) if risk > 0 else None

        # 위험도 (RR 기반)
        if rr is None:
            risk_level = "중간"
        elif rr >= 5:
            risk_level = "낮음"
        elif rr >= 3:
            risk_level = "중간"
        else:
            risk_level = "높음"

        out.append({
            "종목코드":       c.get("ticker", ""),
            "종목명":         c.get("name", ""),
            "시장":           c.get("market", ""),
            "점수":           c.get("score", 0),
            "분류":           classification,
            "현재가":         close,
            "분할매수_low":   min(prices) if prices else 0,
            "분할매수_high":  max(prices) if prices else 0,
            "손절가":         sl_dante,
            "손절률(%)":      sl_pct,
            "목표가":         target,
            "추천수익률(%)":  recommended_pct,
            "RR":             rr,
            "위험도":         risk_level,
            "블랙리스트":     bool(c.get("blacklisted")),
        })
    return pd.DataFrame(out)


# ============================================================
# 가격 데이터 — DB 직접 조회 (캐시 비활성)
# ============================================================
def get_close_at(ticker: str, date_str: str) -> Optional[float]:
    """date_str(YYYY-MM-DD 또는 YYYYMMDD) 의 종가 — 그 날 거래없으면 None."""
    d = date_str.replace("-", "")
    with db_connect() as conn:
        row = conn.execute(
            "SELECT 종가 FROM daily_data WHERE 종목코드=? AND 날짜=?",
            (ticker, int(d)),
        ).fetchone()
    return float(row[0]) if row else None


def get_close_after_n_business_days(
    ticker: str, ref_date_yyyymmdd: str, n_days: int
) -> tuple[Optional[float], Optional[str]]:
    """ref_date 다음 n번째 거래일의 종가 + 그 날짜 (YYYYMMDD).
    n=0 → ref_date 본인. n=1 → 다음 영업일."""
    with db_connect() as conn:
        cur = conn.execute(
            """SELECT 날짜, 종가 FROM daily_data
               WHERE 종목코드=? AND 날짜>=? ORDER BY 날짜 ASC LIMIT ?""",
            (ticker, int(ref_date_yyyymmdd), n_days + 1),
        ).fetchall()
    if len(cur) > n_days:
        d, c = cur[n_days]
        return float(c), str(d)
    return None, None


def get_ohlcv_series(
    ticker: str, days_back: int = 180, ref_date: Optional[str] = None
) -> pd.DataFrame:
    """차트용 OHLCV 시계열 (180거래일 기본). ref_date 이전 N거래일."""
    with db_connect() as conn:
        if ref_date:
            d = ref_date.replace("-", "")
            df = pd.read_sql_query(
                """SELECT 날짜, 시가, 고가, 저가, 종가, 거래량 FROM daily_data
                   WHERE 종목코드=? AND 날짜<=? ORDER BY 날짜 DESC LIMIT ?""",
                conn, params=(ticker, int(d), days_back),
            )
        else:
            df = pd.read_sql_query(
                """SELECT 날짜, 시가, 고가, 저가, 종가, 거래량 FROM daily_data
                   WHERE 종목코드=? ORDER BY 날짜 DESC LIMIT ?""",
                conn, params=(ticker, days_back),
            )
    if df.empty:
        return df
    df["날짜"] = pd.to_datetime(df["날짜"].astype(str), format="%Y%m%d")
    df = df.sort_values("날짜").reset_index(drop=True)
    return df


def get_indicator_series(
    ticker: str, days_back: int = 180, ref_date: Optional[str] = None
) -> pd.DataFrame:
    """차트 오버레이용 — base_std / span_a_std / span_b_std / ma60 / ma224."""
    with db_connect() as conn:
        if ref_date:
            d = ref_date.replace("-", "")
            df = pd.read_sql_query(
                """SELECT 날짜, base_std, span_a_std, span_b_std, ma60, ma224
                   FROM daily_indicators WHERE 종목코드=? AND 날짜<=?
                   ORDER BY 날짜 DESC LIMIT ?""",
                conn, params=(ticker, int(d), days_back),
            )
        else:
            df = pd.read_sql_query(
                """SELECT 날짜, base_std, span_a_std, span_b_std, ma60, ma224
                   FROM daily_indicators WHERE 종목코드=?
                   ORDER BY 날짜 DESC LIMIT ?""",
                conn, params=(ticker, days_back),
            )
    if df.empty:
        return df
    df["날짜"] = pd.to_datetime(df["날짜"].astype(str), format="%Y%m%d")
    df = df.sort_values("날짜").reset_index(drop=True)
    return df


# ============================================================
# 성과 계산
# ============================================================
def compute_returns_at(
    ticker: str, ref_date_yyyymmdd: str, entry_price: float, n_days_list: list[int]
) -> dict:
    """ref_date 의 entry_price 대비 N거래일 후 종가 수익률 dict.
    Returns: {1: {date, close, ret_pct}, 5: ..., 10: ..., 20: ...}"""
    out = {}
    for n in n_days_list:
        close, date = get_close_after_n_business_days(ticker, ref_date_yyyymmdd, n)
        if close is None or entry_price <= 0:
            out[n] = {"date": None, "close": None, "ret_pct": None}
        else:
            out[n] = {
                "date": date,
                "close": close,
                "ret_pct": round((close - entry_price) / entry_price * 100, 2),
            }
    return out


def get_kospi_return(ref_date_yyyymmdd: str, n_days: int) -> Optional[float]:
    """ref_date 기준 KOSPI 시총합계(proxy) N거래일 후 수익률 %."""
    with db_connect() as conn:
        cur = conn.execute(
            """SELECT 날짜, SUM(시가총액) AS k FROM daily_data
               WHERE 날짜>=? GROUP BY 날짜 ORDER BY 날짜 ASC LIMIT ?""",
            (int(ref_date_yyyymmdd), n_days + 1),
        ).fetchall()
    if len(cur) <= n_days:
        return None
    base = float(cur[0][1])
    after = float(cur[n_days][1])
    if base <= 0:
        return None
    return round((after - base) / base * 100, 2)


def aggregate_history_performance(n_days: int = 5) -> pd.DataFrame:
    """전체 history 의 모든 추천 종목에 대해 N일 수익률 집계."""
    rows = []
    for date_str in list_history_dates():
        data = load_candidates(date_str)
        ref = data.get("date", "")
        if not ref:
            continue
        for c in data.get("tradable_candidates") or []:
            t = c.get("ticker", "")
            entry = c.get("close", 0)
            close, _ = get_close_after_n_business_days(t, ref, n_days)
            if close is None or entry <= 0:
                continue
            rows.append({
                "추천일":       date_str,
                "종목코드":     t,
                "종목명":       c.get("name", ""),
                "추천일종가":   entry,
                f"+{n_days}일종가": close,
                f"+{n_days}일수익률(%)": round((close - entry) / entry * 100, 2),
            })
    return pd.DataFrame(rows)


# ============================================================
# 포맷팅 헬퍼
# ============================================================
def risk_emoji(level: str) -> str:
    return {"낮음": "🟢", "중간": "🟡", "높음": "🔴"}.get(level, "🟡")


def fmt_won(v) -> str:
    try:
        return f"{int(v):,}원"
    except Exception:
        return str(v)


def fmt_pct(v, signed: bool = True) -> str:
    if v is None or pd.isna(v):
        return "-"
    if signed:
        return f"{float(v):+.2f}%"
    return f"{float(v):.2f}%"
