"""국장 자동매매 시스템 - 5단계 백테스팅 (backtesting.py)

5년치 데이터로 단테 전략을 검증한다.

입력
----
- stock_data.db   : daily_data + daily_indicators + ticker_master
- (참조) calculator.py 로직, dante_rules_v2.json

매매 로직 (요약)
----------------
매수  : 점수 ≥3 + entry_possible + 추천 목표 >15% + 당일 거래대금 ≥1억 → 다음날 시가 매수
        분할매수 1:2:4:8 (실제 KRW 10만/20만/40만/30만), 실제 도달한 단계만 체결
매도  : 장중 고가 ≥ 목표 → 잔량 매도 (익절)
        장중 저가 ≤ 1차 손절 → 50% 매도
        장중 저가 ≤ 2차 손절 → 잔량 매도
        최대 보유 기간 초과 → 시가 청산 (스윙 15 / 스윙_중장기 42 / 중장기 126 영업일)
비용  : 매수/매도 수수료 0.015%, 거래세 0.23%, 슬리피지 0.1%

한계
----
- 현재 상장 종목만 대상 → 생존 편향 있음
- 동일 종목 중복 보유 금지 (포지션 겹치지 않음)
- 호가 단위 스냅 계산 이후 체결

출력
----
- backtest_results.json  (전체 통계 + 연도/점수/포지션별 + TOP10 승패)

실행
----
python backtesting.py
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import json
import gc
import time
import argparse
import logging
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ============================================================
# 경로
# ============================================================
BASE_DIR = Path(r"C:\Users\sji48\ksat_gang")
DB_PATH = BASE_DIR / "stock_data.db"
OUT_PATH = BASE_DIR / "backtest_results_v4.json"
CHECKPOINT_PATH = BASE_DIR / "backtest_checkpoint.json"
KOSPI_CACHE_PATH = BASE_DIR / "kospi_index_cache.csv"
LOG_DIR = BASE_DIR / "logs"

BASE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 설정
# ============================================================
CONFIG: dict = {
    "version": "v4",
    "start_date": "20210423",
    "end_date": "20260422",

    # 자본
    "initial_capital": 10_000_000,
    "max_per_stock": 1_000_000,
    # 1:2:4:8 에 1,000,000 한도 반영 → 100k/200k/400k/300k
    "stage_amounts": [100_000, 200_000, 400_000, 300_000],

    # 매수 조건 — 점수 4점만 (v1 백테스트에서 4점이 최고 실적)
    "target_scores": [4],                    # v1은 [3,4,5,6,7,8] 이었음
    "min_recommended_pct_exclusive": 15.0,   # > 15% 만 (= warning 없음)
    "min_daily_trading_value": 100_000_000,  # 1억 (최대 1M 투입이 그 1%)

    # 시장 regime 필터 (v2=True; v4=False — 단테 철학: 지수 무관)
    "enable_bear_market_filter": False,
    "kospi_index_ticker": "1001",
    "bear_market_ma_period": 200,

    # 개별 종목 MA224 필터 (v4 ⭐): 종가 > 224일선일 때만 매수
    "require_above_ma224": True,
    "require_above_ma112": False,           # v6: True — 종가 > 112일선
    "require_box_pattern": False,           # v6: True — 60일 박스권(변동성 ≤ 임계)
    "box_range_max": 0.30,                  # 60일 (max-min)/min ≤ 30%

    # v5 변경점 (기본값은 False → v2 동작 유지)
    "use_min_target_for_swing_mid": False,  # v5: True — 스윙_중장기는 3목표 중 최소값 사용
    "disable_sl2": False,                   # v5: True — 2차 손절(구름하단) 제거
    "sl1_full_exit": False,                 # v5: True — 1차 손절 시 반매도 대신 전량
    "enable_trailing_stop": False,          # v5: True
    "trailing_activation_pct": 5.0,         # +5% 도달 시 트레일링 활성
    "trailing_drawdown_pct": 2.0,           # 최고점 대비 -2%면 매도

    # v6 변경점
    "target_strategy": "median",            # v6: "prior_high" (120일 고점만 사용)
    "simple_stop_loss_pct": None,           # v6: -0.05 — 매수 평단가 대비 -5% 단일 손절
                                            # (1차/2차 손절 로직 대체)

    # 종목명 기반 추가 필터
    "exclude_preferred_stocks": False,      # True: 종목명 끝이 '우' 또는 '우B'인 종목 제외

    # v4 — 손실 분석 기반 개선
    "require_base_line_near": False,              # True: 진입 조건에 기준선 ±2% 근접 강제
    "require_not_overheated_entry": False,        # True: 기준선 이격 <7% (과열 아님) 강제
    "re_entry_cooldown_days": 0,                  # >0: 매도 후 N 영업일 재진입 금지
    "blacklist_enabled": False,                   # True: 블랙리스트 규칙 활성
    "blacklist_lookback": 5,                      # 최근 N거래 집계
    "blacklist_threshold": 3,                     # M회 손절 → 블랙리스트
    "blacklist_ban_days": 252,                    # N영업일(=1년) 금지

    # 매수가 (오프셋)
    "split_buy_pct_offsets": [0.00, -0.03, -0.06, -0.09],

    # 손절
    "stop_loss_1_base_deviation_pct": -0.015,

    # 목표가
    "wave_lookback_days": 60,
    "wave_energy_multiplier": 1.5,
    "prior_high_lookback_days": 120,
    "rr_multiplier": 5,

    # 포지션 타입
    "position_type_thresholds": {
        "swing_max_pct": 15.0,
        "mid_max_pct": 30.0,
    },
    "max_hold_days": {
        "스윙": 15,
        "스윙_중장기": 42,
        "중장기": 126,
    },

    # 비용
    "buy_commission_rate": 0.00015,
    "sell_commission_rate": 0.00015,
    "sell_tax_rate": 0.0023,
    "slippage_rate": 0.001,

    # 통계
    "risk_free_annual": 0.025,
    "trading_days_per_year": 252,

    # 로그
    "log_progress_every_days": 50,
    "checkpoint_every_days": 100,
}


# ============================================================
# 로깅
# ============================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("backtest")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = TimedRotatingFileHandler(
        LOG_DIR / "backtest.log",
        when="midnight", interval=1, backupCount=30, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


log = setup_logging()


# ============================================================
# 호가 단위 (calculator.py 와 동일)
# ============================================================
def snap_to_tick(price: float) -> int:
    if price is None or price <= 0:
        return 0
    if price < 1000:
        tick = 1
    elif price < 5000:
        tick = 5
    elif price < 10000:
        tick = 10
    elif price < 50000:
        tick = 50
    elif price < 100000:
        tick = 100
    elif price < 500000:
        tick = 500
    else:
        tick = 1000
    return int(round(price / tick) * tick)


# ============================================================
# 데이터 로딩 + 지표/신호 계산 (벡터화)
# ============================================================
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-400000")  # 400MB
    return conn


def load_kospi_regime(start: str, end: str, cfg: dict) -> dict[str, bool]:
    """{날짜: True/False} — True = KOSPI 종가 > 200MA (매수 허용).

    - pykrx 로 KOSPI 종합지수 (ticker=1001) 받아와 로컬 CSV 캐시.
    - 200 영업일 이평선 계산 가능하도록 요청 시작일에서 400 달력일 앞까지 fetch.
    - 캐시가 충분히 덮는 경우 네트워크 호출 생략.
    - 필터 비활성 시 또는 fetch 실패 시 빈 dict 반환 → 필터 무시 (전부 allowed).
    """
    if not cfg.get("enable_bear_market_filter", False):
        log.info("bear-market 필터 비활성")
        return {}

    from datetime import timedelta
    fetch_start = (datetime.strptime(start, "%Y%m%d") - timedelta(days=400)).strftime("%Y%m%d")

    df = None
    if KOSPI_CACHE_PATH.exists():
        try:
            df = pd.read_csv(KOSPI_CACHE_PATH, dtype={"날짜": str})
            if df["날짜"].min() <= fetch_start and df["날짜"].max() >= end:
                log.info(f"KOSPI 캐시 사용 ({len(df)}행)")
            else:
                log.info("KOSPI 캐시 범위 부족 → 재 fetch")
                df = None
        except Exception as e:
            log.warning(f"KOSPI 캐시 로드 실패: {e}")
            df = None

    if df is None:
        # 방법 1: pykrx 로 KOSPI 종합지수 (1001) fetch — KRX_ID 필요
        try:
            from pykrx import stock as pystock
            raw = pystock.get_index_ohlcv(fetch_start, end, cfg["kospi_index_ticker"])
            if raw is None or raw.empty:
                raise RuntimeError("빈 결과")
            raw = raw.reset_index()
            if hasattr(raw["날짜"].iloc[0], "strftime"):
                raw["날짜"] = raw["날짜"].dt.strftime("%Y%m%d")
            else:
                raw["날짜"] = raw["날짜"].astype(str).str.replace("-", "")
            df = raw[["날짜", "종가"]].copy()
            log.info(f"  pykrx KOSPI {len(df)}행 fetch 성공")
        except Exception as e:
            log.warning(f"pykrx KOSPI fetch 실패 → DB proxy(KOSPI 시가총액 합계) 사용: {e}")
            df = None

        # 방법 2 (fallback): DB의 KOSPI 전체 시가총액 합계 proxy
        if df is None:
            try:
                with db_connect() as conn:
                    q = """
                    SELECT 날짜, SUM(시가총액) AS 종가
                    FROM daily_data
                    WHERE 시장구분 = 'KOSPI' AND 날짜 BETWEEN ? AND ?
                    GROUP BY 날짜
                    ORDER BY 날짜
                    """
                    df = pd.read_sql_query(q, conn, params=(fetch_start, end))
                df["날짜"] = df["날짜"].astype(str)
                log.info(
                    f"  proxy: KOSPI 시가총액 합계 사용 ({len(df)}행)"
                    " — 정확한 지수는 아니지만 시장 방향성은 강한 상관관계"
                )
            except Exception as e:
                log.warning(f"proxy 생성 실패 → 필터 비활성: {e}")
                return {}

        df.to_csv(KOSPI_CACHE_PATH, index=False, encoding="utf-8")
        log.info(f"  KOSPI 캐시 저장 → {KOSPI_CACHE_PATH.name}")

    df = df.sort_values("날짜").reset_index(drop=True)
    ma = df["종가"].rolling(cfg["bear_market_ma_period"], min_periods=cfg["bear_market_ma_period"]).mean()
    df["above"] = (df["종가"] > ma).fillna(False)

    regime = dict(zip(df["날짜"].astype(str).tolist(), df["above"].astype(bool).tolist()))
    n_bull = sum(regime.values())
    n_total = len(regime)
    log.info(f"KOSPI regime: {n_total}일 / 상승 {n_bull}일 ({100*n_bull/max(n_total,1):.1f}%) / 하락 {n_total-n_bull}일")
    return regime


def load_merged_data(start: str, end: str, cfg: dict | None = None) -> pd.DataFrame:
    """OHLCV + 지표 + 종목명 (스팩 / 저가주 / 선택적 우선주 제외) 단일 쿼리."""
    cfg = cfg if cfg is not None else CONFIG
    log.info(f"데이터 로딩 {start} ~ {end}")
    t0 = time.time()
    # 우선주 제외 필터 (종목명 끝이 '우' 또는 '우B')
    pref_clause = ""
    if cfg.get("exclude_preferred_stocks", False):
        pref_clause = "AND m.종목명 NOT LIKE '%우' AND m.종목명 NOT LIKE '%우B'"
    q = f"""
    SELECT d.날짜, d.종목코드,
           d.시가, d.고가, d.저가, d.종가, d.거래량, d.거래대금,
           i.base_std,
           i.span_a_std, i.span_b_std,
           i.span_a_2x, i.span_b_2x,
           i.ma5, i.ma20, i.ma60, i.ma112, i.ma224,
           i.bb_lower, i.vol_ratio,
           m.종목명, m.시장구분
    FROM daily_data d
    INNER JOIN ticker_master m ON d.종목코드 = m.종목코드
    LEFT JOIN daily_indicators i ON d.날짜 = i.날짜 AND d.종목코드 = i.종목코드
    WHERE d.날짜 BETWEEN ? AND ?
      AND m.종목명 NOT LIKE '%스팩%'
      AND d.종가 >= 1000
      {pref_clause}
    """
    with db_connect() as conn:
        df = pd.read_sql_query(q, conn, params=(start, end))
    if cfg.get("exclude_preferred_stocks", False):
        log.info(f"  (우선주 제외 필터 적용)")
    log.info(f"  로드 완료: {len(df):,} 행, {time.time()-t0:.1f}s")
    return df


def compute_rolling_stats(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """종목별 60일 wave(고/저), 120일 전고점 rolling 계산."""
    log.info("rolling 고/저 계산")
    t0 = time.time()
    df = df.sort_values(["종목코드", "날짜"]).reset_index(drop=True)
    grp = df.groupby("종목코드", sort=False)
    w = cfg["wave_lookback_days"]
    p = cfg["prior_high_lookback_days"]
    df["wave_high"] = grp["고가"].transform(lambda s: s.rolling(w, min_periods=w).max())
    df["wave_low"] = grp["저가"].transform(lambda s: s.rolling(w, min_periods=w).min())
    df["prior_high"] = grp["고가"].transform(lambda s: s.rolling(p, min_periods=p).max())
    log.info(f"  완료: {time.time()-t0:.1f}s")
    return df


def compute_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """조건 8개 평가 + score + 목표가 + entry_possible + signal 컬럼 추가."""
    log.info("조건/점수/신호 계산")
    t0 = time.time()

    close = df["종가"].astype(float)
    low = df["저가"].astype(float)
    span_a = df["span_a_std"]
    span_b = df["span_b_std"]
    span_a_2x = df["span_a_2x"]
    span_b_2x = df["span_b_2x"]
    base = df["base_std"]
    ma5 = df["ma5"]
    ma20 = df["ma20"]
    ma60 = df["ma60"]
    bb_lower = df["bb_lower"]
    vol_ratio = df["vol_ratio"]

    # 8개 조건 (NaN 안전: NaN 과의 비교는 False)
    c1 = (close > np.maximum(span_a, span_b))                               # cloud_above_std
    c2 = (close > np.maximum(span_a_2x, span_b_2x))                         # cloud_above_2x
    dev_base = (close - base).abs() / close
    c3 = (dev_base <= 0.02)                                                 # base_line_near
    ma_max = np.maximum(np.maximum(ma5, ma20), ma60)
    ma_min = np.minimum(np.minimum(ma5, ma20), ma60)
    c4 = ((ma_max - ma_min) / ma_min <= 0.02)                               # ma_convergence
    c5 = (vol_ratio >= 2.0)                                                 # volume_surge
    c6 = (vol_ratio >= 3.0)                                                 # accumulation_bar
    c7 = (low <= bb_lower)                                                  # bb_lower_touch
    c8 = (dev_base < 0.07)                                                  # base_line_not_overheated

    # NaN → False (합산용)
    conds = [c1, c2, c3, c4, c5, c6, c7, c8]
    for i, c in enumerate(conds):
        conds[i] = c.fillna(False).astype(np.int8)
    score = conds[0] + conds[1] + conds[2] + conds[3] + conds[4] + conds[5] + conds[6] + conds[7]
    df["score"] = score.astype(np.int8)
    # v4 필터용: 조건 3(기준선 근접)과 조건 8(과열 아님)을 컬럼으로 저장
    df["_cond_base_line_near"] = conds[2]
    df["_cond_not_overheated"] = conds[7]

    # 손절 / 목표
    sl1_raw = base * (1.0 + cfg["stop_loss_1_base_deviation_pct"])
    df["sl1_raw"] = sl1_raw
    df["sl2_raw"] = span_b  # 선행스팬2 = 2차 손절 = 구름 하단

    wave_range = (df["wave_high"] - df["wave_low"]).clip(lower=0)
    target_a = close + wave_range * cfg["wave_energy_multiplier"]
    target_b = df["prior_high"]
    risk = (close - sl1_raw).clip(lower=0)
    target_c = close + risk * cfg["rr_multiplier"]

    # 중간값 (3 항목 median) + 최소값 (v5 스윙_중장기용)
    tgt_stack = np.stack([target_a.values, target_b.values, target_c.values], axis=1)
    target_med = np.nanmedian(tgt_stack, axis=1)
    target_min = np.nanmin(tgt_stack, axis=1)
    df["target_a"] = target_a
    df["target_b"] = target_b
    df["target_c"] = target_c
    df["target_median"] = target_med
    df["target_min"] = target_min

    # target_chosen — 전략에 따라 선택
    ts = cfg.get("target_strategy", "median")
    if ts == "prior_high":
        target_chosen = target_b.values
    elif ts == "min":
        target_chosen = target_min
    elif ts == "max":
        target_chosen = np.nanmax(tgt_stack, axis=1)
    else:  # "median"
        target_chosen = target_med
    df["target_chosen"] = target_chosen
    df["recommended_pct"] = (df["target_chosen"].values - close.values) / close.values * 100

    # entry_possible = sl1 < 종가
    df["entry_possible"] = (sl1_raw < close).fillna(False)

    # 포지션 타입
    thr = cfg["position_type_thresholds"]
    pt = np.where(
        df["recommended_pct"] <= thr["swing_max_pct"], "스윙",
        np.where(df["recommended_pct"] <= thr["mid_max_pct"], "스윙_중장기", "중장기"),
    )
    df["position_type"] = pt

    # 최종 신호 — score는 target_scores 집합 내에 들어와야 통과
    base_signal = (
        df["score"].isin(cfg["target_scores"])
        & df["entry_possible"]
        & (df["recommended_pct"] > cfg["min_recommended_pct_exclusive"])
        & (df["거래대금"] >= cfg["min_daily_trading_value"])
        & df["sl1_raw"].notna()
        & df["sl2_raw"].notna()
        & df["target_median"].notna()
    )
    # allowed_position_types 필터 (옵션)
    allowed_types = cfg.get("allowed_position_types")
    if allowed_types:
        base_signal = base_signal & df["position_type"].isin(allowed_types)

    # v4 — 진입 조건 강화
    if cfg.get("require_base_line_near", False):
        base_signal = base_signal & (df["_cond_base_line_near"] == 1)
    if cfg.get("require_not_overheated_entry", False):
        base_signal = base_signal & (df["_cond_not_overheated"] == 1)

    n_before_extra = int(base_signal.sum())
    final_signal = base_signal
    filter_log: list[str] = []

    # v4: 개별 종목 224일선 필터
    if cfg.get("require_above_ma224", False):
        ma224_ok = (df["종가"] > df["ma224"]) & df["ma224"].notna()
        before = int(final_signal.sum())
        final_signal = final_signal & ma224_ok
        after = int(final_signal.sum())
        filter_log.append(f"MA224 {before:,}→{after:,} ({before-after:,} 제외)")

    # v6: 개별 종목 112일선 필터
    if cfg.get("require_above_ma112", False):
        ma112_ok = (df["종가"] > df["ma112"]) & df["ma112"].notna()
        before = int(final_signal.sum())
        final_signal = final_signal & ma112_ok
        after = int(final_signal.sum())
        filter_log.append(f"MA112 {before:,}→{after:,} ({before-after:,} 제외)")

    # v6: 60일 박스권 패턴 — (wave_high - wave_low) / wave_low ≤ box_range_max
    if cfg.get("require_box_pattern", False):
        box_max = cfg.get("box_range_max", 0.30)
        wl = df["wave_low"]
        wh = df["wave_high"]
        box_range = (wh - wl) / wl.where(wl > 0)
        box_ok = (box_range <= box_max) & box_range.notna()
        before = int(final_signal.sum())
        final_signal = final_signal & box_ok
        after = int(final_signal.sum())
        filter_log.append(f"BOX≤{box_max*100:.0f}% {before:,}→{after:,} ({before-after:,} 제외)")

    df["signal"] = final_signal
    n_after_total = int(df["signal"].sum())
    if filter_log:
        log.info(
            f"  완료: {time.time()-t0:.1f}s / target_scores={cfg['target_scores']} / "
            f"신호 {n_before_extra:,} → " + " / ".join(filter_log) + f" → 최종 {n_after_total:,}"
        )
    else:
        log.info(
            f"  완료: {time.time()-t0:.1f}s / target_scores={cfg['target_scores']} / "
            f"신호 {n_before_extra:,} 건"
        )
    return df


# ============================================================
# 포지션
# ============================================================
@dataclass
class Position:
    ticker: str
    name: str
    market: str
    score: int
    open_signal_date: str   # 신호 발생일
    open_date: str          # 실제 매수 첫날 (신호 다음날)
    position_type: str
    max_hold_days: int

    # 가격 레벨 (snap 완료)
    stage_prices: list       # [p1, p2, p3, p4]
    stage_amounts: list      # 원본 할당 KRW (지속적으로 실제 소요 비용 비교)
    sl1: int
    sl2: int
    target: int
    recommended_pct: float

    # 체결 상태
    stage_filled: list = field(default_factory=lambda: [False, False, False, False])
    stage_shares: list = field(default_factory=lambda: [0, 0, 0, 0])
    stage_costs: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])  # 실제 현금 지출

    remaining_shares: int = 0
    total_bought_shares: int = 0
    total_cost: float = 0.0        # 수수료 포함 실제 현금 지출

    # 매도
    sl1_triggered: bool = False
    sell_events: list = field(default_factory=list)   # [{date, reason, shares, price, proceeds}]
    total_proceeds: float = 0.0

    closed: bool = False
    close_date: str | None = None

    # 트레일링 스톱 (v5)
    trailing_active: bool = False
    peak_price_since_activation: float = 0.0

    def avg_cost_per_share(self) -> float:
        if self.total_bought_shares == 0:
            return 0.0
        return self.total_cost / self.total_bought_shares

    def realized_pnl(self) -> float:
        return self.total_proceeds - self.total_cost

    def realized_pnl_pct(self) -> float:
        if self.total_cost <= 0:
            return 0.0
        return self.realized_pnl() / self.total_cost * 100


# ============================================================
# 거래 비용 헬퍼
# ============================================================
def buy_execution(order_price: float, amount_krw: float, cfg: dict) -> tuple[int, float]:
    """주어진 KRW 한도 내 매수. (체결 주수, 실제 현금 지출) 반환."""
    slippage = 1.0 + cfg["slippage_rate"]
    exec_price = order_price * slippage
    commission = 1.0 + cfg["buy_commission_rate"]
    if exec_price <= 0:
        return 0, 0.0
    # 수수료 포함 1주당 비용
    cost_per_share = exec_price * commission
    shares = int(amount_krw // cost_per_share)
    if shares <= 0:
        return 0, 0.0
    actual_cost = shares * exec_price * commission
    return shares, actual_cost


def sell_execution(order_price: float, shares: int, cfg: dict) -> float:
    """주어진 주수 매도 → 수취 현금 반환."""
    slippage = 1.0 - cfg["slippage_rate"]
    exec_price = order_price * slippage
    fee_tax = cfg["sell_commission_rate"] + cfg["sell_tax_rate"]
    proceeds = shares * exec_price * (1.0 - fee_tax)
    return max(proceeds, 0.0)


# ============================================================
# 시뮬레이션
# ============================================================
def _get_max_hold_days(position_type: str, cfg: dict) -> int:
    mh = cfg["max_hold_days"]
    return mh.get(position_type, mh["중장기"])


def simulate(
    df: pd.DataFrame, cfg: dict, kospi_regime: dict[str, bool] | None = None
) -> tuple[list[Position], list[tuple[str, float]], dict]:
    log.info("시뮬레이션 시작")
    t_start = time.time()
    kospi_regime = kospi_regime or {}
    bear_filter_active = bool(kospi_regime)
    n_bear_skipped = 0
    n_signals_considered = 0
    n_signals_opened = 0

    # 보조 구조: 날짜순 리스트 + (date, ticker) 조회용 dict
    trading_dates = sorted(df["날짜"].unique().tolist())
    log.info(f"  거래일 {len(trading_dates)}일 ({trading_dates[0]} ~ {trading_dates[-1]})")

    # OHLCV 조회용 dict: (date, ticker) -> (시가, 고가, 저가, 종가)
    log.info("  OHLCV 룩업 테이블 구성")
    ohlcv_cols = df[["날짜", "종목코드", "시가", "고가", "저가", "종가"]]
    ohlcv_lookup: dict[tuple[str, str], tuple[int, int, int, int]] = {
        (r.날짜, r.종목코드): (int(r.시가), int(r.고가), int(r.저가), int(r.종가))
        for r in ohlcv_cols.itertuples(index=False)
    }
    log.info(f"  룩업 {len(ohlcv_lookup):,} 항목")

    # 신호 → 날짜별 그룹
    sig_df = df[df["signal"]][[
        "날짜", "종목코드", "종목명", "시장구분", "score",
        "종가", "sl1_raw", "sl2_raw",
        "target_median", "target_min", "target_chosen", "recommended_pct",
        "position_type",
    ]].copy()
    signals_by_date: dict[str, pd.DataFrame] = dict(list(sig_df.groupby("날짜", sort=False)))

    date_idx = {d: i for i, d in enumerate(trading_dates)}

    cash = float(cfg["initial_capital"])
    open_positions: list[Position] = []
    closed_positions: list[Position] = []
    held_tickers: set[str] = set()  # 동시 보유 중인 종목
    portfolio_history: list[tuple[str, float]] = []

    stage_amounts = cfg["stage_amounts"]

    # v4 상태 추적
    cooldown_days = int(cfg.get("re_entry_cooldown_days", 0))
    bl_enabled = bool(cfg.get("blacklist_enabled", False))
    bl_lookback = int(cfg.get("blacklist_lookback", 5))
    bl_threshold = int(cfg.get("blacklist_threshold", 3))
    bl_ban = int(cfg.get("blacklist_ban_days", 252))
    last_sell_idx: dict[str, int] = {}                  # ticker → 날짜 인덱스
    recent_results: dict[str, list[bool]] = {}          # ticker → [True/False: 수익]
    blacklist_until_idx: dict[str, int] = {}            # ticker → 해제 날짜 인덱스
    n_skipped_cooldown = 0
    n_skipped_blacklist = 0
    blacklist_log: list[tuple[str, str, str]] = []      # (ticker, name, date)

    for i, date in enumerate(trading_dates):
        # --------------- 1) 오늘자 이벤트 처리: 보유 포지션 ---------------
        still_open: list[Position] = []
        for pos in open_positions:
            row = ohlcv_lookup.get((date, pos.ticker))
            if row is None:
                # 당일 거래 정지 등 → 다음날로 이월
                still_open.append(pos)
                continue
            o, h, l, c = row
            pos_closed_today = False

            # 1a) 오늘 가능하면 추가 분할 체결 (장중 저가가 스테이지 가격 이하로 내려왔는지)
            for idx in range(4):
                if pos.stage_filled[idx]:
                    continue
                stage_price = pos.stage_prices[idx]
                # 미체결 스테이지 가격 이하로 저가가 내려왔으면 체결 가능
                if l <= stage_price:
                    # 해당 날짜 첫 체결은 시가가 이미 스테이지 가격 이하인 경우 시가로 체결
                    fill_base = min(o, stage_price) if o <= stage_price else stage_price
                    if cash >= stage_amounts[idx]:
                        shares, cost = buy_execution(fill_base, stage_amounts[idx], cfg)
                        if shares > 0:
                            pos.stage_filled[idx] = True
                            pos.stage_shares[idx] = shares
                            pos.stage_costs[idx] = cost
                            pos.remaining_shares += shares
                            pos.total_bought_shares += shares
                            pos.total_cost += cost
                            cash -= cost

            # 1b) 트레일링 스톱 (v5) — 매도 판정은 "어제까지의 peak" 기준으로 수행.
            # peak 갱신은 하루 이벤트 처리 끝난 뒤(매수 포함 다 처리 후) 별도로 진행.
            trailing_sell_level: int | None = None
            if cfg.get("enable_trailing_stop", False) and pos.remaining_shares > 0:
                avg = pos.avg_cost_per_share()
                activation = cfg["trailing_activation_pct"] / 100.0
                drawdown = cfg["trailing_drawdown_pct"] / 100.0
                # 활성화: peak(= 어제까지 최고가)이 avg*1.05 이상 도달했으면 활성화
                if avg > 0 and not pos.trailing_active:
                    if pos.peak_price_since_activation >= avg * (1 + activation):
                        pos.trailing_active = True
                # 활성 상태면 "어제까지 peak * (1-drawdown)"이 오늘의 매도 레벨
                if pos.trailing_active:
                    trailing_sell_level = snap_to_tick(
                        pos.peak_price_since_activation * (1 - drawdown)
                    )

            # 1c) 매도 체크
            simple_stop_pct = cfg.get("simple_stop_loss_pct")
            if simple_stop_pct is not None:
                # v6: 매수 평단가 × (1 + simple_stop_pct) 이탈 시 전량 청산
                avg_cost = pos.avg_cost_per_share()
                simple_stop_lv = snap_to_tick(avg_cost * (1.0 + simple_stop_pct)) if avg_cost > 0 else 0
                if pos.remaining_shares > 0 and simple_stop_lv > 0 and l <= simple_stop_lv:
                    sell_shares = pos.remaining_shares
                    proceeds = sell_execution(simple_stop_lv, sell_shares, cfg)
                    pos.sell_events.append({
                        "date": date, "reason": "단일손절", "shares": sell_shares,
                        "price": simple_stop_lv, "proceeds": proceeds,
                    })
                    pos.remaining_shares = 0
                    pos.total_proceeds += proceeds
                    cash += proceeds
                    pos.closed = True
                    pos.close_date = date
                    pos_closed_today = True
                # 목표 체크
                if not pos.closed and pos.remaining_shares > 0 and h >= pos.target:
                    sell_shares = pos.remaining_shares
                    proceeds = sell_execution(pos.target, sell_shares, cfg)
                    pos.sell_events.append({
                        "date": date, "reason": "익절", "shares": sell_shares,
                        "price": pos.target, "proceeds": proceeds,
                    })
                    pos.remaining_shares = 0
                    pos.total_proceeds += proceeds
                    cash += proceeds
                    pos.closed = True
                    pos.close_date = date
                    pos_closed_today = True
                # v6 경로는 1차/2차/트레일링 로직 전부 스킵
                # (아래 if/else 절은 건너뜀)
                pass
            elif (sl2_enabled := not cfg.get("disable_sl2", False)) and pos.remaining_shares > 0 and l <= pos.sl2:
                # 2차 손절 → 전량 매도 at sl2
                sell_shares = pos.remaining_shares
                proceeds = sell_execution(pos.sl2, sell_shares, cfg)
                pos.sell_events.append({
                    "date": date, "reason": "2차손절", "shares": sell_shares,
                    "price": pos.sl2, "proceeds": proceeds,
                })
                pos.remaining_shares = 0
                pos.total_proceeds += proceeds
                cash += proceeds
                pos.closed = True
                pos.close_date = date
                pos_closed_today = True
            else:
                sl1_full = cfg.get("sl1_full_exit", False)
                # v5: 트레일링 스톱 (활성 + 장중 저가가 트레일링 레벨 이하로 내려옴)
                if (trailing_sell_level is not None
                    and pos.remaining_shares > 0
                    and l <= trailing_sell_level):
                    sell_shares = pos.remaining_shares
                    proceeds = sell_execution(trailing_sell_level, sell_shares, cfg)
                    pos.sell_events.append({
                        "date": date, "reason": "트레일링", "shares": sell_shares,
                        "price": trailing_sell_level, "proceeds": proceeds,
                    })
                    pos.remaining_shares = 0
                    pos.total_proceeds += proceeds
                    cash += proceeds
                    pos.closed = True
                    pos.close_date = date
                    pos_closed_today = True

                # 1차 손절
                if not pos.closed and pos.remaining_shares > 0 and not pos.sl1_triggered and l <= pos.sl1:
                    if sl1_full:
                        sell_shares = pos.remaining_shares  # v5: 전량
                    else:
                        sell_shares = pos.remaining_shares // 2  # v2: 반
                    if sell_shares > 0:
                        proceeds = sell_execution(pos.sl1, sell_shares, cfg)
                        pos.sell_events.append({
                            "date": date, "reason": "1차손절", "shares": sell_shares,
                            "price": pos.sl1, "proceeds": proceeds,
                        })
                        pos.remaining_shares -= sell_shares
                        pos.total_proceeds += proceeds
                        cash += proceeds
                    pos.sl1_triggered = True
                    if sl1_full and pos.remaining_shares == 0:
                        pos.closed = True
                        pos.close_date = date
                        pos_closed_today = True

                # 목표 (잔량 매도)
                if not pos.closed and pos.remaining_shares > 0 and h >= pos.target:
                    sell_shares = pos.remaining_shares
                    proceeds = sell_execution(pos.target, sell_shares, cfg)
                    pos.sell_events.append({
                        "date": date, "reason": "익절", "shares": sell_shares,
                        "price": pos.target, "proceeds": proceeds,
                    })
                    pos.remaining_shares = 0
                    pos.total_proceeds += proceeds
                    cash += proceeds
                    pos.closed = True
                    pos.close_date = date
                    pos_closed_today = True

            # 1c) 최대 보유 기간 체크 (아직 안 닫혔고 오늘까지 경과일 >= max_hold)
            if not pos_closed_today and not pos.closed:
                elapsed = date_idx[date] - date_idx[pos.open_date]
                if elapsed >= pos.max_hold_days and pos.remaining_shares > 0:
                    # 오늘 시가로 잔량 청산
                    sell_shares = pos.remaining_shares
                    proceeds = sell_execution(o, sell_shares, cfg)
                    pos.sell_events.append({
                        "date": date, "reason": "기간초과", "shares": sell_shares,
                        "price": o, "proceeds": proceeds,
                    })
                    pos.remaining_shares = 0
                    pos.total_proceeds += proceeds
                    cash += proceeds
                    pos.closed = True
                    pos.close_date = date
                    pos_closed_today = True

            # 1e) 트레일링 peak 갱신 (오늘 이벤트 처리 후, 내일을 위한 준비)
            if cfg.get("enable_trailing_stop", False) and not pos.closed:
                pos.peak_price_since_activation = max(
                    pos.peak_price_since_activation, h
                )

            if pos.closed:
                closed_positions.append(pos)
                held_tickers.discard(pos.ticker)

                # v4 상태 업데이트: 쿨다운 & 블랙리스트
                last_sell_idx[pos.ticker] = i
                won = pos.realized_pnl() > 0
                recent_results.setdefault(pos.ticker, []).append(won)
                if bl_enabled:
                    recent_N = recent_results[pos.ticker][-bl_lookback:]
                    losses = sum(1 for w in recent_N if not w)
                    if losses >= bl_threshold and pos.ticker not in blacklist_until_idx:
                        blacklist_until_idx[pos.ticker] = i + bl_ban
                        blacklist_log.append((pos.ticker, pos.name, date))
                    elif losses >= bl_threshold:
                        # 이미 등재된 경우 연장하지 않음 (간단 정책)
                        pass
            else:
                still_open.append(pos)
        open_positions = still_open

        # --------------- 2) 신호 발생일(오늘) → 다음 영업일에 포지션 오픈 ---------------
        # v2: 약세장(KOSPI 200MA 미만)일 경우 신규 진입 스킵
        date_is_bull = True
        if bear_filter_active:
            date_is_bull = kospi_regime.get(date, True)  # 누락은 허용

        if i + 1 < len(trading_dates) and date_is_bull:
            next_date = trading_dates[i + 1]
            today_signals = signals_by_date.get(date)
            if today_signals is not None and len(today_signals):
                # 점수 내림차순으로 정렬해 우선순위 부여
                for sig in today_signals.sort_values(
                    ["score", "종목코드"], ascending=[False, True]
                ).itertuples(index=False):
                    n_signals_considered += 1
                    if cash < stage_amounts[0]:
                        break  # 1차조차 불가능
                    ticker = sig.종목코드
                    if ticker in held_tickers:
                        continue  # 이미 보유 중

                    # v4: 재매수 쿨다운 체크
                    if cooldown_days > 0 and ticker in last_sell_idx:
                        if (i + 1) - last_sell_idx[ticker] < cooldown_days:
                            n_skipped_cooldown += 1
                            continue

                    # v4: 블랙리스트 체크
                    if bl_enabled and ticker in blacklist_until_idx:
                        if (i + 1) < blacklist_until_idx[ticker]:
                            n_skipped_blacklist += 1
                            continue

                    # 다음 영업일 OHLCV 확인 (시가 매수)
                    next_row = ohlcv_lookup.get((next_date, ticker))
                    if next_row is None:
                        continue
                    next_open = next_row[0]
                    if next_open <= 0:
                        continue

                    # 스테이지 가격 (snap)
                    base_price = float(sig.종가)
                    stage_prices = [
                        snap_to_tick(base_price * (1.0 + off))
                        for off in cfg["split_buy_pct_offsets"]
                    ]
                    sl1 = snap_to_tick(float(sig.sl1_raw))
                    sl2 = snap_to_tick(float(sig.sl2_raw))
                    # v5: 스윙_중장기는 최소값 목표 선택
                    if (cfg.get("use_min_target_for_swing_mid", False)
                        and sig.position_type == "스윙_중장기"):
                        target_base = float(sig.target_min)
                    else:
                        target_base = float(sig.target_chosen)
                    target = snap_to_tick(target_base)

                    pos = Position(
                        ticker=ticker,
                        name=sig.종목명 or "",
                        market=sig.시장구분 or "",
                        score=int(sig.score),
                        open_signal_date=sig.날짜,
                        open_date=next_date,
                        position_type=sig.position_type,
                        max_hold_days=_get_max_hold_days(sig.position_type, cfg),
                        stage_prices=stage_prices,
                        stage_amounts=list(stage_amounts),
                        sl1=sl1,
                        sl2=sl2,
                        target=target,
                        recommended_pct=float(sig.recommended_pct),
                    )

                    # 1차 매수 = 다음날 시가 기준 체결
                    shares, cost = buy_execution(next_open, stage_amounts[0], cfg)
                    if shares == 0:
                        continue
                    pos.stage_filled[0] = True
                    pos.stage_shares[0] = shares
                    pos.stage_costs[0] = cost
                    pos.remaining_shares += shares
                    pos.total_bought_shares += shares
                    pos.total_cost += cost
                    cash -= cost

                    # v5 트레일링 초기 peak = 매수 시가 (다음날 이후부터 갱신)
                    pos.peak_price_since_activation = float(next_open)

                    open_positions.append(pos)
                    held_tickers.add(ticker)
                    n_signals_opened += 1
        elif i + 1 < len(trading_dates) and not date_is_bull:
            # 약세장 — 오늘의 신호를 기록만 하고 스킵
            today_signals = signals_by_date.get(date)
            if today_signals is not None:
                n_bear_skipped += len(today_signals)

        # --------------- 3) 포트폴리오 평가 (종가 기준 mark-to-market) ---------------
        pv = cash
        for pos in open_positions:
            row = ohlcv_lookup.get((date, pos.ticker))
            if row is None:
                # 마지막 알려진 가격 가정 — 간단화: 평단가로 평가
                pv += pos.remaining_shares * pos.avg_cost_per_share()
            else:
                pv += pos.remaining_shares * row[3]  # 종가
        portfolio_history.append((date, pv))

        # --------------- 4) 로그 / 체크포인트 ---------------
        if (i + 1) % cfg["log_progress_every_days"] == 0:
            elapsed = time.time() - t_start
            log.info(
                f"  [{date}] {i+1}/{len(trading_dates)} "
                f"cash={cash:,.0f} open={len(open_positions)} closed={len(closed_positions)} "
                f"pv={pv:,.0f} / {elapsed:.0f}s"
            )
        if (i + 1) % cfg["checkpoint_every_days"] == 0:
            _save_checkpoint({
                "last_date": date,
                "cash": cash,
                "open_count": len(open_positions),
                "closed_count": len(closed_positions),
                "pv": pv,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })

    # 루프 종료 후 남은 포지션 강제 청산 (마지막 거래일 종가)
    last_date = trading_dates[-1]
    for pos in open_positions:
        row = ohlcv_lookup.get((last_date, pos.ticker))
        if row is None:
            # 남은 평단가 기준
            pos.closed = True
            pos.close_date = last_date
            continue
        if pos.remaining_shares > 0:
            proceeds = sell_execution(row[3], pos.remaining_shares, cfg)
            pos.sell_events.append({
                "date": last_date, "reason": "백테스트종료", "shares": pos.remaining_shares,
                "price": row[3], "proceeds": proceeds,
            })
            pos.total_proceeds += proceeds
            cash += proceeds
            pos.remaining_shares = 0
        pos.closed = True
        pos.close_date = last_date
        closed_positions.append(pos)
    open_positions.clear()

    elapsed = time.time() - t_start
    log.info(
        f"시뮬레이션 완료 — 거래 {len(closed_positions):,} 건 / "
        f"최종 현금 {cash:,.0f} / {elapsed:.1f}s"
    )
    if bear_filter_active:
        log.info(
            f"bear 필터: 고려 {n_signals_considered}, 오픈 {n_signals_opened}, "
            f"약세장 스킵 {n_bear_skipped}"
        )
    sim_meta = {
        "n_signals_considered": n_signals_considered,
        "n_signals_opened": n_signals_opened,
        "n_bear_skipped": n_bear_skipped,
        "bear_filter_active": bear_filter_active,
        "n_skipped_cooldown": n_skipped_cooldown,
        "n_skipped_blacklist": n_skipped_blacklist,
        "blacklist_log": blacklist_log,
    }
    return closed_positions, portfolio_history, sim_meta


# ============================================================
# 체크포인트 (진행 상황만)
# ============================================================
def _save_checkpoint(data: dict) -> None:
    try:
        fd, tmp = tempfile.mkstemp(prefix=".bt_", suffix=".tmp", dir=str(CHECKPOINT_PATH.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CHECKPOINT_PATH)
    except Exception as e:
        log.warning(f"체크포인트 저장 실패: {e}")


# ============================================================
# 통계
# ============================================================
def _year_of(date_str: str) -> int:
    return int(date_str[:4])


def compute_statistics(
    trades: list[Position],
    pv_history: list[tuple[str, float]],
    cfg: dict,
) -> dict:
    log.info("통계 계산")
    if not trades:
        return {"warning": "거래 없음", "total_trades": 0}

    initial = float(cfg["initial_capital"])

    # 거래별 수익률
    returns = [t.realized_pnl_pct() for t in trades]
    pnl_abs = [t.realized_pnl() for t in trades]

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    # 포트폴리오 시계열
    pv_df = pd.DataFrame(pv_history, columns=["date", "pv"])
    pv_df["date_dt"] = pd.to_datetime(pv_df["date"], format="%Y%m%d")
    pv_df = pv_df.sort_values("date_dt").reset_index(drop=True)
    pv = pv_df["pv"].astype(float).values

    # 누적 수익률
    final_pv = float(pv[-1]) if len(pv) else initial
    cum_ret_pct = (final_pv - initial) / initial * 100

    # CAGR
    first_dt = pv_df["date_dt"].iloc[0]
    last_dt = pv_df["date_dt"].iloc[-1]
    years = (last_dt - first_dt).days / 365.25
    cagr = ((final_pv / initial) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    # MDD
    cummax = np.maximum.accumulate(pv)
    drawdowns = (pv - cummax) / cummax
    mdd_pct = float(drawdowns.min() * 100) if len(drawdowns) else 0.0

    # 샤프 (일간 수익률 기준)
    daily_rets = pd.Series(pv).pct_change().dropna().values
    if len(daily_rets) > 1 and daily_rets.std() > 0:
        rf_daily = cfg["risk_free_annual"] / cfg["trading_days_per_year"]
        sharpe = (daily_rets.mean() - rf_daily) / daily_rets.std() * np.sqrt(
            cfg["trading_days_per_year"]
        )
    else:
        sharpe = 0.0

    overall = {
        "total_trades": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "avg_return_pct": round(float(np.mean(returns)), 2),
        "avg_win_pct": round(float(np.mean(wins)), 2) if wins else 0.0,
        "avg_loss_pct": round(float(np.mean(losses)), 2) if losses else 0.0,
        "max_win_pct": round(float(np.max(returns)), 2) if returns else 0.0,
        "max_loss_pct": round(float(np.min(returns)), 2) if returns else 0.0,
        "cumulative_return_pct": round(cum_ret_pct, 2),
        "cagr_pct": round(cagr, 2),
        "mdd_pct": round(mdd_pct, 2),
        "sharpe": round(float(sharpe), 2),
        "initial_capital": initial,
        "final_portfolio_value": round(final_pv, 0),
        "period_days": int((last_dt - first_dt).days),
    }

    # 연도별
    by_year: dict[str, dict] = {}
    trades_by_year: dict[int, list[Position]] = {}
    for t in trades:
        y = _year_of(t.close_date or t.open_date)
        trades_by_year.setdefault(y, []).append(t)
    for y, ts in sorted(trades_by_year.items()):
        rs = [p.realized_pnl_pct() for p in ts]
        ws = [r for r in rs if r > 0]
        by_year[str(y)] = {
            "trades": len(ts),
            "wins": len(ws),
            "win_rate_pct": round(len(ws) / len(ts) * 100, 2) if ts else 0,
            "avg_return_pct": round(float(np.mean(rs)), 2),
            "total_pnl_krw": round(sum(p.realized_pnl() for p in ts), 0),
        }

    # 점수별
    by_score: dict[str, dict] = {}
    for sc in range(3, 9):
        ts = [t for t in trades if t.score == sc]
        if not ts:
            continue
        rs = [p.realized_pnl_pct() for p in ts]
        ws = [r for r in rs if r > 0]
        by_score[str(sc)] = {
            "trades": len(ts),
            "win_rate_pct": round(len(ws) / len(ts) * 100, 2),
            "avg_return_pct": round(float(np.mean(rs)), 2),
            "total_pnl_krw": round(sum(p.realized_pnl() for p in ts), 0),
        }

    # 포지션 타입별
    by_ptype: dict[str, dict] = {}
    for pt in ("스윙", "스윙_중장기", "중장기"):
        ts = [t for t in trades if t.position_type == pt]
        if not ts:
            continue
        rs = [p.realized_pnl_pct() for p in ts]
        ws = [r for r in rs if r > 0]
        by_ptype[pt] = {
            "trades": len(ts),
            "win_rate_pct": round(len(ws) / len(ts) * 100, 2),
            "avg_return_pct": round(float(np.mean(rs)), 2),
            "avg_hold_days": round(
                float(np.mean([_days_between(t.open_date, t.close_date or t.open_date) for t in ts])),
                1,
            ),
            "total_pnl_krw": round(sum(p.realized_pnl() for p in ts), 0),
        }

    # 종목별 집계 TOP 10 승/패 (종목별 누적 PnL)
    per_ticker: dict[str, dict] = {}
    for t in trades:
        d = per_ticker.setdefault(t.ticker, {
            "ticker": t.ticker, "name": t.name, "trades": 0,
            "total_pnl_krw": 0.0, "total_cost_krw": 0.0,
        })
        d["trades"] += 1
        d["total_pnl_krw"] += t.realized_pnl()
        d["total_cost_krw"] += t.total_cost
    # 수익률 계산
    for d in per_ticker.values():
        d["avg_return_pct"] = round(d["total_pnl_krw"] / d["total_cost_krw"] * 100, 2) \
            if d["total_cost_krw"] > 0 else 0.0
        d["total_pnl_krw"] = round(d["total_pnl_krw"], 0)
        d["total_cost_krw"] = round(d["total_cost_krw"], 0)

    ranked = sorted(per_ticker.values(), key=lambda x: -x["total_pnl_krw"])
    top_winners = ranked[:10]
    top_losers = ranked[-10:][::-1]  # worst first

    # 매도 사유별 집계 (1차손절/2차손절/익절/기간초과/단일손절/트레일링)
    # 한 포지션이 여러 사유(예: 1차손절 후 익절)를 가질 수 있으므로 sell_event 단위 카운트
    reason_counts: dict[str, int] = {}
    reason_shares: dict[str, int] = {}
    n_sl1_triggered_pos = 0   # 1차 손절 트리거된 포지션 수 (포지션 단위)
    n_sl2_triggered_pos = 0
    for t in trades:
        if t.sl1_triggered:
            n_sl1_triggered_pos += 1
        for ev in t.sell_events:
            r = ev.get("reason", "?")
            reason_counts[r] = reason_counts.get(r, 0) + 1
            reason_shares[r] = reason_shares.get(r, 0) + int(ev.get("shares", 0))
            if r == "2차손절":
                n_sl2_triggered_pos += 1
    by_close_reason = {
        "event_counts": reason_counts,
        "event_share_totals": reason_shares,
        "positions_with_sl1_trigger": n_sl1_triggered_pos,
        "positions_with_sl2_trigger": n_sl2_triggered_pos,
        "sl1_trigger_pct": round(n_sl1_triggered_pos / len(trades) * 100, 2) if trades else 0.0,
        "sl2_trigger_pct": round(n_sl2_triggered_pos / len(trades) * 100, 2) if trades else 0.0,
    }

    return {
        "overall": overall,
        "by_year": by_year,
        "by_score": by_score,
        "by_position_type": by_ptype,
        "by_close_reason": by_close_reason,
        "top10_winners_by_ticker": top_winners,
        "top10_losers_by_ticker": top_losers,
        "portfolio_value_history_sample": [
            {"date": d, "pv": round(v, 0)}
            for (d, v) in pv_history[::max(1, len(pv_history) // 200)]  # ~200 샘플
        ],
    }


def _days_between(a: str, b: str) -> int:
    da = datetime.strptime(a, "%Y%m%d")
    db = datetime.strptime(b, "%Y%m%d")
    return (db - da).days


# ============================================================
# 메인
# ============================================================
def save_json_atomic(payload: dict, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run() -> int:
    t_all = time.time()
    log.info("=" * 60)
    log.info("단테 전략 백테스팅 시작")
    log.info(f"  기간: {CONFIG['start_date']} ~ {CONFIG['end_date']}")
    log.info(f"  초기 자본: {CONFIG['initial_capital']:,}원")
    log.info("=" * 60)

    kospi_regime = load_kospi_regime(CONFIG["start_date"], CONFIG["end_date"], CONFIG)

    df = load_merged_data(CONFIG["start_date"], CONFIG["end_date"])
    df = compute_rolling_stats(df, CONFIG)
    df = compute_signals(df, CONFIG)

    # 메모리 절감: 신호 생성 후 필요 컬럼만 유지
    trades, pv_history, sim_meta = simulate(df, CONFIG, kospi_regime)
    del df
    gc.collect()

    stats = compute_statistics(trades, pv_history, CONFIG)

    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "period_start": CONFIG["start_date"],
            "period_end": CONFIG["end_date"],
            "config_snapshot": CONFIG,
            "simulation_meta": sim_meta,
            "limitations": [
                "현재 상장 종목만 대상 (생존 편향 있음)",
                "동일 종목 중복 보유 금지",
                "호가 단위로 가격 스냅 후 체결 가정",
            ],
        },
        "statistics": stats,
    }
    save_json_atomic(payload, OUT_PATH)

    log.info(f"완료 — 총 {time.time()-t_all:.1f}s → {OUT_PATH}")
    ov = stats.get("overall", {})
    log.info(
        "핵심: 거래 %d / 승률 %.2f%% / 누적 %.2f%% / CAGR %.2f%% / MDD %.2f%% / Sharpe %.2f" %
        (
            ov.get("total_trades", 0),
            ov.get("win_rate_pct", 0),
            ov.get("cumulative_return_pct", 0),
            ov.get("cagr_pct", 0),
            ov.get("mdd_pct", 0),
            ov.get("sharpe", 0),
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="단테 전략 백테스팅")
    parser.parse_args()
    try:
        return run()
    except KeyboardInterrupt:
        log.warning("사용자 중단 (Ctrl+C)")
        return 130
    except Exception as e:
        log.exception(f"치명적 오류: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
