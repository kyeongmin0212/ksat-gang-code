"""국장 자동매매 시스템 - 3단계 단테 전략 (dante_strategy.py)

역할
----
1. 5년치 일봉 데이터(stock_data.db) → 지표 계산 → daily_indicators 테이블 저장
2. 단테 8개 조건 적용 → 종목별 점수 산출
3. 점수 높은 순으로 candidates.json 출력

실행 모드
---------
- python dante_strategy.py --mode init     : 5년치 전체 지표 계산 (2~4시간 예상)
- python dante_strategy.py --mode daily    : 최근 구간 지표 갱신 + 후보 추출
- python dante_strategy.py --mode scan     : 지표 테이블 이미 채워진 상태에서 후보만 추출

체크포인트
----------
indicator_checkpoint.json 사용 (collector의 checkpoint.json 과 별개).
종목 단위로 저장되므로 중단되면 동일 명령으로 자동 재개된다.

참고
----
- dante_rules_v2.json / dante_rules.json : 조건 해석 근거
- filtered_tickers.json : filter.py 통과 종목 (scan 시 이 종목만 평가)
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

import numpy as np
import pandas as pd


# ============================================================
# 경로 / 상수
# ============================================================
BASE_DIR = Path(r"C:\Users\sji48\ksat_gang")
DB_PATH = BASE_DIR / "stock_data.db"
FILTER_PATH = BASE_DIR / "filtered_tickers.json"
CANDIDATES_PATH = BASE_DIR / "candidates.json"
CANDIDATES_V4_PATH = BASE_DIR / "candidates_v4.json"
CHECKPOINT_PATH = BASE_DIR / "indicator_checkpoint.json"
LOG_DIR = BASE_DIR / "logs"

# ─── v4 전략 상수 (2021-2026 백테스팅 확정값) ────────────────────
V4_MID_LONG_PCT_THRESHOLD = 30.0   # 중장기 = 추천 목표 수익률 ≥30%
V4_BASE_LINE_NEAR_PCT = 0.02       # 기준선 ±2% 근접
V4_NOT_OVERHEATED_PCT = 0.07       # 이격도 <7%
V4_WAVE_LOOKBACK_DAYS = 60         # 파동 에너지 계산 윈도우
V4_PRIOR_HIGH_LOOKBACK_DAYS = 120  # 전고점 윈도우
V4_RR_MULTIPLIER = 5               # 리스크:리워드 = 1:5
V4_WAVE_MULTIPLIER = 1.5           # 파동 에너지 × 1.5
V4_KOSPI_MA_PERIOD = 200           # KOSPI 시총 proxy 200MA
V4_SL1_BASE_DEV_PCT = -0.015       # 기준선 -1.5%
# 백테스팅 블랙리스트 (최근 5거래 중 3회 이상 손절로 등재된 종목)
V4_BLACKLIST = {
    "066790": "씨씨에스",
    "340360": "다보링크",
    "094480": "갤럭시아머니트리",
    "332570": "PS일렉트로닉스",
    "042940": "상지건설",
    "380540": "옵티코어",
}
# 분할매수 참고 (calculator.py 와 동일 — 1종목 최대 100만원)
V4_STAGE_AMOUNTS = [100_000, 200_000, 400_000, 300_000]
V4_STAGE_OFFSETS = [0.00, -0.03, -0.06, -0.09]

BASE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 설정 (전부 CONFIG 에서 조정 가능 — 나중에 백테스팅으로 최적화)
# ============================================================
CONFIG: dict = {
    # ---------- 일목균형표 파라미터 ----------
    # 표준 (전환선 9일 / 기준선 26일 / 선행스팬2 52일, 선행 shift 26일)
    "ichimoku_std": {
        "conv": 9, "base": 26, "span_b": 52, "shift": 26,
    },
    # 단테식 2배 버전 (표준 파라미터를 전부 2배) — dante_rules_v2.json 근거
    # "저는 2배 구름대를 봅니다" 발언 반영
    "ichimoku_2x": {
        "conv": 18, "base": 52, "span_b": 104, "shift": 52,
    },

    # ---------- 이평선 (단테식 계단: 5·20·60·112·224·448) ----------
    "ma_periods": [5, 20, 60, 112, 224, 448],

    # ---------- 볼린저 밴드 ----------
    "bb_period": 20,
    "bb_std_mult": 2.0,

    # ---------- 거래량 ----------
    "vol_ma_period": 20,

    # ---------- 조건 임계값 (dante_rules_v2.json base_line_rules 근거) ----------
    # 기준선 ±2% 이내 = 매수 후보
    "base_line_near_pct": 0.02,
    # MA5/20/60 상호 2% 이내 = 이평 수렴
    "ma_convergence_pct": 0.02,
    # 거래량이 20일 평균 대비 2배(=100% 터진다) 이상이면 거래량 급증
    # "100% 터진다" = 평상시 대비 100% 증가 = 2배 → ratio >= 2.0
    "volume_surge_ratio": 2.0,
    # 매집봉: 20일 평균 대비 3배(=300% 터진다) 이상
    "accumulation_bar_ratio": 3.0,
    # 기준선 과열: |close-base|/close >= 7%
    "base_line_overheated_pct": 0.07,

    # ---------- 조건 → 점수 ----------
    # 현재 모든 조건 동일 가중치 1점. 백테스팅 후 차등 가능.
    "condition_weights": {
        "cloud_above_std": 1,
        "cloud_above_2x": 1,
        "base_line_near": 1,
        "ma_convergence": 1,
        "volume_surge": 1,
        "accumulation_bar": 1,
        "bb_lower_touch": 1,
        "base_line_not_overheated": 1,
    },

    # ---------- 최소 히스토리 (지표 계산 가능 최소 일수) ----------
    # 가장 긴 창: span_b_2x(104) + shift_2x(52) = 156일.
    # 안전하게 200일로 설정.
    "min_history_days": 200,

    # ---------- 배치 / 쓰기 ----------
    "upsert_batch_rows": 2000,
    "log_progress_every": 50,
}


# ============================================================
# 로깅 (collector.py 스타일 통일)
# ============================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("dante_strategy")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = TimedRotatingFileHandler(
        LOG_DIR / "dante_strategy.log",
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
# DB
# ============================================================
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-200000")  # 200MB
    return conn


def db_init_indicators():
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_indicators (
                날짜         TEXT NOT NULL,
                종목코드     TEXT NOT NULL,
                -- 일목 표준 (9/26/52)
                conv_std     REAL,
                base_std     REAL,
                span_a_std   REAL,
                span_b_std   REAL,
                -- 일목 2배 (18/52/104)
                conv_2x      REAL,
                base_2x      REAL,
                span_a_2x    REAL,
                span_b_2x    REAL,
                -- 이평선 (단테식 계단)
                ma5          REAL,
                ma20         REAL,
                ma60         REAL,
                ma112        REAL,
                ma224        REAL,
                ma448        REAL,
                -- 볼린저밴드 20일
                bb_mid       REAL,
                bb_upper     REAL,
                bb_lower     REAL,
                -- 거래량
                vol_ma20     REAL,
                vol_ratio    REAL,
                PRIMARY KEY (날짜, 종목코드)
            );

            CREATE INDEX IF NOT EXISTS idx_ind_ticker_date
                ON daily_indicators(종목코드, 날짜);
            CREATE INDEX IF NOT EXISTS idx_ind_date
                ON daily_indicators(날짜);
            """
        )
        conn.commit()
    log.info("daily_indicators 테이블 준비 완료")


# ============================================================
# 체크포인트 (atomic)
# ============================================================
DEFAULT_CHECKPOINT: dict = {
    "mode": None,
    "base_date": None,
    "started_at": None,
    "updated_at": None,
    "total_tickers": 0,
    "completed_tickers": [],
    "failed_tickers": [],
}


def load_checkpoint() -> dict:
    if not CHECKPOINT_PATH.exists():
        return dict(DEFAULT_CHECKPOINT)
    try:
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            cp = json.load(f)
        for k, v in DEFAULT_CHECKPOINT.items():
            cp.setdefault(k, v)
        return cp
    except Exception as e:
        log.error(f"체크포인트 로드 실패 - 초기화: {e}")
        return dict(DEFAULT_CHECKPOINT)


def save_json_atomic(payload: dict, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_checkpoint(cp: dict) -> None:
    cp["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_json_atomic(cp, CHECKPOINT_PATH)


# ============================================================
# 지표 계산
# ============================================================
def compute_indicators_for_ticker(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """종목 1개의 시계열 DataFrame → 지표 DataFrame 반환.

    df는 날짜 오름차순 정렬된 '날짜', '고가', '저가', '종가', '거래량' 컬럼을 가져야 한다.
    반환값은 동일 날짜 인덱스에 NaN 포함 지표 값(초반 구간은 NaN).
    """
    out = pd.DataFrame({"날짜": df["날짜"].values})

    high = df["고가"].astype(float)
    low = df["저가"].astype(float)
    close = df["종가"].astype(float)
    volume = df["거래량"].astype(float)

    # ---- 일목 표준 ----
    p = cfg["ichimoku_std"]
    conv_std = (high.rolling(p["conv"]).max() + low.rolling(p["conv"]).min()) / 2
    base_std = (high.rolling(p["base"]).max() + low.rolling(p["base"]).min()) / 2
    span_a_raw = (conv_std + base_std) / 2
    span_b_raw = (high.rolling(p["span_b"]).max() + low.rolling(p["span_b"]).min()) / 2
    # 선행: 계산일보다 shift 일 앞(미래)에 표시 → DB에는 "표시되는 날짜" 기준 저장
    span_a_std = span_a_raw.shift(p["shift"])
    span_b_std = span_b_raw.shift(p["shift"])

    out["conv_std"] = conv_std.values
    out["base_std"] = base_std.values
    out["span_a_std"] = span_a_std.values
    out["span_b_std"] = span_b_std.values

    # ---- 일목 2배 ----
    p2 = cfg["ichimoku_2x"]
    conv_2x = (high.rolling(p2["conv"]).max() + low.rolling(p2["conv"]).min()) / 2
    base_2x = (high.rolling(p2["base"]).max() + low.rolling(p2["base"]).min()) / 2
    span_a_2x_raw = (conv_2x + base_2x) / 2
    span_b_2x_raw = (high.rolling(p2["span_b"]).max() + low.rolling(p2["span_b"]).min()) / 2
    span_a_2x = span_a_2x_raw.shift(p2["shift"])
    span_b_2x = span_b_2x_raw.shift(p2["shift"])

    out["conv_2x"] = conv_2x.values
    out["base_2x"] = base_2x.values
    out["span_a_2x"] = span_a_2x.values
    out["span_b_2x"] = span_b_2x.values

    # ---- 이평선 (상장 미만 기간은 자동으로 NaN) ----
    for period in cfg["ma_periods"]:
        out[f"ma{period}"] = close.rolling(period).mean().values

    # ---- 볼린저 ----
    bb_mid = close.rolling(cfg["bb_period"]).mean()
    bb_std = close.rolling(cfg["bb_period"]).std(ddof=0)
    out["bb_mid"] = bb_mid.values
    out["bb_upper"] = (bb_mid + cfg["bb_std_mult"] * bb_std).values
    out["bb_lower"] = (bb_mid - cfg["bb_std_mult"] * bb_std).values

    # ---- 거래량 ----
    vol_ma20 = volume.rolling(cfg["vol_ma_period"]).mean()
    out["vol_ma20"] = vol_ma20.values
    # 0 나눗셈 방지
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(vol_ma20 > 0, volume / vol_ma20, np.nan)
    out["vol_ratio"] = ratio

    return out


# ============================================================
# DB 쓰기
# ============================================================
INDICATOR_COLS = [
    "conv_std", "base_std", "span_a_std", "span_b_std",
    "conv_2x", "base_2x", "span_a_2x", "span_b_2x",
    "ma5", "ma20", "ma60", "ma112", "ma224", "ma448",
    "bb_mid", "bb_upper", "bb_lower",
    "vol_ma20", "vol_ratio",
]


def upsert_indicators(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
    """NaN 없는 행만 upsert. 반환값 = 저장 행 수."""
    # NaN이 전부인 행은 건너뛰고, 하나라도 값 있으면 저장 (NaN → None)
    mask = df[INDICATOR_COLS].notna().any(axis=1)
    valid = df.loc[mask]
    if valid.empty:
        return 0

    rows = []
    for rec in valid.to_dict("records"):
        row = [rec["날짜"], ticker]
        for c in INDICATOR_COLS:
            v = rec[c]
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                row.append(None)
            else:
                row.append(float(v))
        rows.append(tuple(row))

    placeholder = ",".join(["?"] * (2 + len(INDICATOR_COLS)))
    col_list = ",".join(["날짜", "종목코드", *INDICATOR_COLS])
    update_set = ", ".join([f"{c} = excluded.{c}" for c in INDICATOR_COLS])

    sql = f"""
    INSERT INTO daily_indicators ({col_list})
    VALUES ({placeholder})
    ON CONFLICT(날짜, 종목코드) DO UPDATE SET {update_set}
    """

    # 배치 단위로 나눠 executemany
    batch = CONFIG["upsert_batch_rows"]
    n_written = 0
    for i in range(0, len(rows), batch):
        conn.executemany(sql, rows[i:i + batch])
        n_written += min(batch, len(rows) - i)
    return n_written


# ============================================================
# 데이터 로딩
# ============================================================
def load_ticker_ohlcv(
    conn: sqlite3.Connection, ticker: str, date_from: str | None = None
) -> pd.DataFrame:
    """단일 종목의 OHLCV 시계열 (날짜 오름차순)."""
    if date_from:
        cur = conn.execute(
            """
            SELECT 날짜, 고가, 저가, 종가, 거래량
            FROM daily_data
            WHERE 종목코드 = ? AND 날짜 >= ?
            ORDER BY 날짜 ASC
            """,
            (ticker, date_from),
        )
    else:
        cur = conn.execute(
            """
            SELECT 날짜, 고가, 저가, 종가, 거래량
            FROM daily_data
            WHERE 종목코드 = ?
            ORDER BY 날짜 ASC
            """,
            (ticker,),
        )
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=["날짜", "고가", "저가", "종가", "거래량"])
    return pd.DataFrame(rows, columns=["날짜", "고가", "저가", "종가", "거래량"])


def list_distinct_tickers(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("SELECT DISTINCT 종목코드 FROM daily_data ORDER BY 종목코드")
    return [r[0] for r in cur.fetchall()]


def load_filtered_tickers() -> list[str]:
    if not FILTER_PATH.exists():
        log.warning(f"{FILTER_PATH} 없음 — 전종목 대상으로 스캔 (filter.py 먼저 실행 권장)")
        return []
    data = json.loads(FILTER_PATH.read_text(encoding="utf-8"))
    return list(data.get("filtered_list", []))


# ============================================================
# init / daily — 지표 계산 루프
# ============================================================
def run_compute(mode: str, daily_from_date: str | None = None) -> None:
    """mode: 'init' → 전종목 전체 재계산, 'daily' → 최근 구간만 재계산."""
    db_init_indicators()

    with db_connect() as conn:
        all_tickers = list_distinct_tickers(conn)
        # 현재 처리 기준일: daily_data 의 최신 날짜.
        # 이 값이 체크포인트의 base_date 와 다르면 다른 날의 잔재이므로 새 세션을 시작한다.
        # (2026-05-07 사고: 5/7 중단 체크포인트를 5/8 실행이 그대로 이어받아 1130종목 누락)
        cur_row = conn.execute("SELECT MAX(날짜) FROM daily_data").fetchone()
        current_base_date = cur_row[0] if cur_row else None
    log.info(f"[{mode}] 처리 대상 {len(all_tickers)}종목 (base_date={current_base_date})")

    cp = load_checkpoint()
    prev_total = cp.get("total_tickers") or 0
    prev_done = len(cp.get("completed_tickers") or [])
    fully_completed_prior = (
        cp.get("mode") == mode and prev_total > 0 and prev_done >= prev_total
    )
    cp_base_date = cp.get("base_date")
    base_date_changed = (
        mode == "daily"
        and current_base_date is not None
        and cp_base_date is not None
        and cp_base_date != current_base_date
    )

    if cp.get("mode") != mode or fully_completed_prior or base_date_changed:
        reason = (
            "mode 변경" if cp.get("mode") != mode
            else "base_date 변경 ({} → {})".format(cp_base_date, current_base_date) if base_date_changed
            else "이전 세션 완료"
        )
        cp = dict(DEFAULT_CHECKPOINT)
        cp["mode"] = mode
        cp["base_date"] = current_base_date
        cp["started_at"] = datetime.now().isoformat(timespec="seconds")
        cp["total_tickers"] = len(all_tickers)
        save_checkpoint(cp)
        log.info(f"[{mode}] 새 세션 시작 (체크포인트 초기화 — {reason})")
    else:
        # 같은 mode + 같은 base_date(또는 cp에 base_date 없음) → 이어서 진행
        cp["total_tickers"] = len(all_tickers)
        if cp.get("base_date") is None and current_base_date is not None:
            cp["base_date"] = current_base_date  # 레거시 체크포인트 보강
        log.info(
            f"[{mode}] 체크포인트 재개 — 이미 완료 {len(cp['completed_tickers'])}, "
            f"실패 {len(cp['failed_tickers'])} (base_date={cp.get('base_date')})"
        )

    completed = set(cp["completed_tickers"])
    failed_set = set(cp["failed_tickers"])

    t_start = time.time()
    processed = 0
    written = 0

    for idx, ticker in enumerate(all_tickers, start=1):
        if ticker in completed:
            continue

        try:
            with db_connect() as conn:
                df = load_ticker_ohlcv(conn, ticker, date_from=daily_from_date)

                if df.empty or len(df) < 2:
                    log.debug(f"[{ticker}] 데이터 부족 — 스킵")
                    completed.add(ticker)
                    continue

                ind = compute_indicators_for_ticker(df, CONFIG)
                n = upsert_indicators(conn, ticker, ind)
                conn.commit()
                written += n

            completed.add(ticker)
            processed += 1

            if processed % CONFIG["log_progress_every"] == 0:
                elapsed = time.time() - t_start
                rate = processed / elapsed if elapsed > 0 else 0
                remain = len(all_tickers) - len(completed)
                eta_min = remain / rate / 60 if rate > 0 else 0
                log.info(
                    f"[{mode}] 진행 {len(completed)}/{len(all_tickers)} "
                    f"({100*len(completed)/len(all_tickers):.1f}%) "
                    f"rate={rate:.1f}/s eta={eta_min:.1f}min written={written:,}"
                )
                cp["completed_tickers"] = sorted(completed)
                cp["failed_tickers"] = sorted(failed_set)
                save_checkpoint(cp)

            # 종목 단위 메모리 해제
            del df, ind
            if processed % 100 == 0:
                gc.collect()

        except Exception as e:
            log.error(f"[{ticker}] 지표 계산 실패 — 스킵: {e}")
            failed_set.add(ticker)
            continue

    cp["completed_tickers"] = sorted(completed)
    cp["failed_tickers"] = sorted(failed_set)
    save_checkpoint(cp)

    elapsed_min = (time.time() - t_start) / 60
    log.info(
        f"[{mode}] 완료 — 처리 {processed}종목, 저장 행 {written:,}, "
        f"실패 {len(failed_set)}, {elapsed_min:.1f}분 경과"
    )


# ============================================================
# 조건 평가 / 점수화
# ============================================================
def evaluate_conditions(row: dict, cfg: dict) -> dict:
    """최신 거래일의 (OHLCV + 지표) 딕셔너리 → 조건별 True/False."""
    close = row.get("종가")
    low = row.get("저가")
    span_a_std = row.get("span_a_std")
    span_b_std = row.get("span_b_std")
    span_a_2x = row.get("span_a_2x")
    span_b_2x = row.get("span_b_2x")
    base_std = row.get("base_std")
    ma5 = row.get("ma5")
    ma20 = row.get("ma20")
    ma60 = row.get("ma60")
    bb_lower = row.get("bb_lower")
    vol_ratio = row.get("vol_ratio")

    def _valid(*vals) -> bool:
        return all(v is not None and np.isfinite(v) for v in vals)

    conds = {
        "cloud_above_std": False,
        "cloud_above_2x": False,
        "base_line_near": False,
        "ma_convergence": False,
        "volume_surge": False,
        "accumulation_bar": False,
        "bb_lower_touch": False,
        "base_line_not_overheated": False,
    }

    # 1) 종가가 표준 구름 위
    if _valid(close, span_a_std, span_b_std):
        conds["cloud_above_std"] = close > max(span_a_std, span_b_std)

    # 2) 종가가 2배 구름 위
    if _valid(close, span_a_2x, span_b_2x):
        conds["cloud_above_2x"] = close > max(span_a_2x, span_b_2x)

    # 3) 기준선 ±2% 이내
    if _valid(close, base_std) and close > 0:
        dev = abs(close - base_std) / close
        conds["base_line_near"] = dev <= cfg["base_line_near_pct"]
        # 8) 기준선 과열 아님 (같은 dev 재사용)
        conds["base_line_not_overheated"] = dev < cfg["base_line_overheated_pct"]

    # 4) MA5 / MA20 / MA60 상호 2% 이내
    if _valid(ma5, ma20, ma60):
        mas = [ma5, ma20, ma60]
        mn, mx = min(mas), max(mas)
        if mn > 0:
            conds["ma_convergence"] = (mx - mn) / mn <= cfg["ma_convergence_pct"]

    # 5) 거래량 급증
    if _valid(vol_ratio):
        conds["volume_surge"] = vol_ratio >= cfg["volume_surge_ratio"]
        # 6) 매집봉 (강한 급증)
        conds["accumulation_bar"] = vol_ratio >= cfg["accumulation_bar_ratio"]

    # 7) 볼린저 하단 터치 (저가가 하단 이하)
    if _valid(low, bb_lower):
        conds["bb_lower_touch"] = low <= bb_lower

    return conds


def score_conditions(conds: dict, weights: dict) -> int:
    return sum(weights.get(k, 0) for k, v in conds.items() if v)


# ============================================================
# v4 — 헬퍼 함수들
# ============================================================
def is_preferred_stock(name: str | None) -> bool:
    """종목명 끝이 '우', '우B', '우C' 면 True."""
    if not name:
        return False
    return name.endswith("우") or name.endswith("우B") or name.endswith("우C")


def check_kospi_bear_market(conn: sqlite3.Connection, ref_date: str) -> dict:
    """KOSPI 시가총액 합계 기반 200MA 약세장 필터.

    Returns {is_bull: bool, kospi_today: float, kospi_ma200: float, sample_dates: int}
    """
    cur = conn.execute(
        """
        SELECT 날짜, SUM(시가총액) AS kospi_total
        FROM daily_data
        WHERE 시장구분 = 'KOSPI' AND 날짜 <= ?
        GROUP BY 날짜
        ORDER BY 날짜 DESC
        LIMIT ?
        """,
        (ref_date, V4_KOSPI_MA_PERIOD + 50),
    )
    rows = cur.fetchall()
    if len(rows) < V4_KOSPI_MA_PERIOD:
        return {"is_bull": True, "kospi_today": 0, "kospi_ma200": 0,
                "sample_dates": len(rows), "note": "200일 누적 데이터 부족 — bull 가정"}
    rows.sort(key=lambda r: r[0])  # ascending
    totals = [float(r[1] or 0) for r in rows]
    today_val = totals[-1]
    ma200 = float(np.mean(totals[-V4_KOSPI_MA_PERIOD:]))
    return {
        "is_bull": today_val >= ma200,
        "kospi_today": today_val,
        "kospi_ma200": ma200,
        "sample_dates": len(rows),
    }


def fetch_targets_for_tickers(
    conn: sqlite3.Connection, ref_date: str, tickers: list[str]
) -> dict[str, dict]:
    """각 종목별 (wave_high_60, wave_low_60, prior_high_120) 배치 조회.

    Returns: {ticker: {"wave_high": h, "wave_low": l, "prior_high": ph}}
    """
    if not tickers:
        return {}
    # 최근 120 영업일 범위
    dates_cur = conn.execute(
        "SELECT DISTINCT 날짜 FROM daily_data WHERE 날짜 <= ? "
        "ORDER BY 날짜 DESC LIMIT ?",
        (ref_date, V4_PRIOR_HIGH_LOOKBACK_DAYS),
    )
    recent_dates = [r[0] for r in dates_cur.fetchall()]
    if not recent_dates:
        return {}
    date_from_120 = min(recent_dates)
    wave_dates = recent_dates[: V4_WAVE_LOOKBACK_DAYS]
    date_from_60 = min(wave_dates) if wave_dates else date_from_120

    placeholders = ",".join("?" * len(tickers))
    # 60일 wave (high + low)
    q_wave = f"""
    SELECT 종목코드, MAX(고가) AS hi, MIN(저가) AS lo
    FROM daily_data
    WHERE 종목코드 IN ({placeholders})
      AND 날짜 BETWEEN ? AND ?
    GROUP BY 종목코드
    """
    wave_rows = conn.execute(q_wave, [*tickers, date_from_60, ref_date]).fetchall()
    wave_map = {r[0]: (float(r[1] or 0), float(r[2] or 0)) for r in wave_rows}

    # 120일 전고점
    q_ph = f"""
    SELECT 종목코드, MAX(고가) AS hi
    FROM daily_data
    WHERE 종목코드 IN ({placeholders})
      AND 날짜 BETWEEN ? AND ?
    GROUP BY 종목코드
    """
    ph_rows = conn.execute(q_ph, [*tickers, date_from_120, ref_date]).fetchall()
    ph_map = {r[0]: float(r[1] or 0) for r in ph_rows}

    out = {}
    for tkr in tickers:
        wh, wl = wave_map.get(tkr, (0.0, 0.0))
        ph = ph_map.get(tkr, 0.0)
        out[tkr] = {"wave_high": wh, "wave_low": wl, "prior_high": ph}
    return out


def _snap_to_tick(price: float) -> int:
    """KRX 호가 단위 반올림 (calculator.py 와 동일)."""
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


def _compute_v4_prices(close: float, base_std: float, span_b_std: float,
                       wave_high: float, wave_low: float, prior_high: float) -> dict:
    """v4 목표가 3종 + 중간값 + 손절 2종 + 분할매수."""
    sl1_raw = base_std * (1.0 + V4_SL1_BASE_DEV_PCT)
    sl1 = _snap_to_tick(sl1_raw)
    sl2 = _snap_to_tick(span_b_std)

    wave_range = max(wave_high - wave_low, 0)
    target_a = close + wave_range * V4_WAVE_MULTIPLIER
    target_b = prior_high if prior_high > 0 else close
    risk = max(close - sl1_raw, 0)
    target_c = close + risk * V4_RR_MULTIPLIER

    targets = sorted([target_a, target_b, target_c])
    target_median = targets[1]
    recommended_pct = (target_median - close) / close * 100 if close > 0 else 0

    # 분할매수 가격
    buy_stages = {}
    for i, (amt, off) in enumerate(zip(V4_STAGE_AMOUNTS, V4_STAGE_OFFSETS), start=1):
        buy_stages[f"{i}차"] = {
            "price": _snap_to_tick(close * (1 + off)),
            "amount_krw": amt,
        }

    rr = round((target_median - close) / risk, 2) if risk > 0 else None

    return {
        "sl1": sl1,
        "sl2": sl2,
        "target_a_wave": _snap_to_tick(target_a),
        "target_b_prior_high": _snap_to_tick(target_b),
        "target_c_rr": _snap_to_tick(target_c),
        "target_median": _snap_to_tick(target_median),
        "recommended_pct": round(recommended_pct, 2),
        "risk_reward_ratio": rr,
        "buy_stages": buy_stages,
    }


def run_scan_v4(ref_date_override: str | None = None) -> None:
    """v4 전략 후보 추출. 백테스팅에서 확정된 조건 적용.

    v4 = v2_dante_A + base_line_near + not_overheated + 쿨다운(실전용) + 블랙리스트 경고
    """
    if not DB_PATH.exists():
        log.error(f"DB 없음: {DB_PATH}")
        return

    with db_connect() as conn:
        if ref_date_override:
            ref_date = ref_date_override
        else:
            ref_date = conn.execute(
                "SELECT MAX(날짜) FROM daily_indicators"
            ).fetchone()[0]
        if not ref_date:
            log.error("daily_indicators 비어있음 — 먼저 --mode init 또는 --mode daily 실행")
            return
        log.info(f"[scan_v4] 기준일 {ref_date}")

        # KOSPI 시장 상태
        kospi_state = check_kospi_bear_market(conn, ref_date)
        bear_market = not kospi_state["is_bull"]
        if bear_market:
            log.warning(f"[scan_v4] ⚠ 약세장 감지 — KOSPI {kospi_state['kospi_today']:,.0f} < "
                        f"200MA {kospi_state['kospi_ma200']:,.0f} → 신규 매수 비권장")
        else:
            log.info(f"[scan_v4] 강세장 — KOSPI {kospi_state['kospi_today']:,.0f} ≥ "
                     f"200MA {kospi_state['kospi_ma200']:,.0f}")

        filtered = set(load_filtered_tickers())
        if filtered:
            log.info(f"[scan_v4] filtered_tickers.json — {len(filtered)}종목 대상")

        cur = conn.execute(
            """
            SELECT d.종목코드, d.시장구분, d.종가, d.저가, d.거래량, d.거래대금,
                   i.base_std, i.span_a_std, i.span_b_std,
                   i.span_a_2x, i.span_b_2x,
                   i.ma5, i.ma20, i.ma60,
                   i.bb_lower, i.vol_ratio,
                   m.종목명
            FROM daily_data d
            JOIN daily_indicators i
              ON d.날짜 = i.날짜 AND d.종목코드 = i.종목코드
            LEFT JOIN ticker_master m ON d.종목코드 = m.종목코드
            WHERE d.날짜 = ?
            """,
            (ref_date,),
        )
        columns = [c[0] for c in cur.description]
        rows = cur.fetchall()

        # 1차 필터링 — score=4 + entry_possible + 우선주 제외 후보 선별
        initial_pool: list[dict] = []
        total_scanned = len(rows)
        removed_by_filter = 0
        removed_by_preferred = 0
        removed_by_score = 0
        removed_by_entry = 0
        removed_by_base_near = 0
        removed_by_overheated = 0

        for raw in rows:
            rec = dict(zip(columns, raw))
            ticker = rec["종목코드"]

            if filtered and ticker not in filtered:
                removed_by_filter += 1
                continue

            name = rec.get("종목명") or ""
            if is_preferred_stock(name):
                removed_by_preferred += 1
                continue

            conds = evaluate_conditions(rec, CONFIG)
            score = score_conditions(conds, CONFIG["condition_weights"])

            if score != 4:  # v4: 정확히 4점만
                removed_by_score += 1
                continue

            # entry_possible = sl1 < 현재가
            close = rec.get("종가")
            base_std = rec.get("base_std")
            if not close or not base_std:
                continue
            sl1 = base_std * (1 + V4_SL1_BASE_DEV_PCT)
            if sl1 >= close:
                removed_by_entry += 1
                continue

            # v4 필수 조건
            if not conds.get("base_line_near"):
                removed_by_base_near += 1
                continue
            if not conds.get("base_line_not_overheated"):
                removed_by_overheated += 1
                continue

            rec["_conditions"] = conds
            rec["_score"] = score
            initial_pool.append(rec)

        log.info(
            f"[scan_v4] 전체 {total_scanned} / filter제외 {removed_by_filter} / "
            f"우선주 {removed_by_preferred} / score≠4 {removed_by_score} / "
            f"entry불가 {removed_by_entry} / base_near미만족 {removed_by_base_near} / "
            f"overheated {removed_by_overheated} / 1차통과 {len(initial_pool)}"
        )

        # 2차: 중장기 분류 (목표 30% 이상) — 60/120일 고저 필요
        tickers_pool = [r["종목코드"] for r in initial_pool]
        targets_map = fetch_targets_for_tickers(conn, ref_date, tickers_pool)

    # 후보 구성 (DB 연결 닫은 후)
    candidates_all: list[dict] = []
    for rec in initial_pool:
        t = rec["종목코드"]
        close = float(rec["종가"])
        base_std = float(rec["base_std"])
        span_b = float(rec.get("span_b_std") or 0)
        tg = targets_map.get(t, {})
        prices = _compute_v4_prices(
            close, base_std, span_b,
            tg.get("wave_high", 0), tg.get("wave_low", 0), tg.get("prior_high", 0),
        )
        # 중장기 아니면 제외
        if prices["recommended_pct"] < V4_MID_LONG_PCT_THRESHOLD:
            continue

        is_blacklisted = t in V4_BLACKLIST
        candidates_all.append({
            "ticker": t,
            "name": rec.get("종목명") or "",
            "market": rec.get("시장구분") or "",
            "score": rec["_score"],
            "close": int(close),
            "conditions": rec["_conditions"],
            "position_type": "중장기",
            "blacklisted": is_blacklisted,
            "blacklist_reason": "백테스팅에서 최근 5거래 중 3회 이상 손절" if is_blacklisted else None,
            "recently_sold": None,  # 실전 기록 없음 — 사용자 수동 트랙
            "indicators_snapshot": {
                "base_std": base_std,
                "span_a_std": rec.get("span_a_std"),
                "span_b_std": span_b,
                "ma5": rec.get("ma5"),
                "ma20": rec.get("ma20"),
                "ma60": rec.get("ma60"),
                "bb_lower": rec.get("bb_lower"),
                "vol_ratio": rec.get("vol_ratio"),
            },
            **prices,  # sl1, sl2, target_*, buy_stages, rr, recommended_pct
        })

    # 정렬 — blacklist 제외 후 추천 목표% 내림차순
    candidates_all.sort(key=lambda x: (x["blacklisted"], -x["recommended_pct"]))

    # 분리
    tradable = [c for c in candidates_all if not c["blacklisted"]]
    blacklisted = [c for c in candidates_all if c["blacklisted"]]

    payload = {
        "date": ref_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": "v4 (점수4 + 중장기 + base_line_near + not_overheated + 약세장필터 + 블랙리스트)",
        "market_state": {
            "is_bull": kospi_state["is_bull"],
            "bear_market": bear_market,
            "kospi_proxy_today": kospi_state["kospi_today"],
            "kospi_proxy_200ma": kospi_state["kospi_ma200"],
            "buy_action_allowed": kospi_state["is_bull"],
            "warning": ("⚠ 약세장 — 신규 매수 중단 권장" if bear_market
                        else "✓ 강세장 — 매수 가능"),
        },
        "filter_summary": {
            "total_scanned": total_scanned,
            "removed_preferred_stocks": removed_by_preferred,
            "removed_score_not_4": removed_by_score,
            "removed_entry_not_possible": removed_by_entry,
            "removed_base_line_not_near": removed_by_base_near,
            "removed_overheated": removed_by_overheated,
            "pool_before_mid_long_filter": len(initial_pool),
            "total_v4_candidates": len(candidates_all),
            "tradable_candidates": len(tradable),
            "blacklisted_count": len(blacklisted),
        },
        "backtest_reference": {
            "period": "2021-04-23 ~ 2026-04-22 (5년)",
            "cagr_pct": 52.95,
            "cumulative_return_pct": 735.81,
            "sharpe": 1.84,
            "mdd_pct": -17.45,
            "win_rate_pct": 76.83,
            "note": "백테스트 수치는 낙관적. 실전 예상 CAGR 25~35%",
        },
        "v4_blacklist": V4_BLACKLIST,
        "v4_config": {
            "mid_long_pct_threshold": V4_MID_LONG_PCT_THRESHOLD,
            "base_line_near_pct": V4_BASE_LINE_NEAR_PCT,
            "not_overheated_pct": V4_NOT_OVERHEATED_PCT,
            "sl1_base_dev_pct": V4_SL1_BASE_DEV_PCT,
            "wave_lookback_days": V4_WAVE_LOOKBACK_DAYS,
            "prior_high_lookback_days": V4_PRIOR_HIGH_LOOKBACK_DAYS,
            "rr_multiplier": V4_RR_MULTIPLIER,
            "stage_amounts": V4_STAGE_AMOUNTS,
            "stage_offsets": V4_STAGE_OFFSETS,
        },
        "tradable_candidates": tradable,
        "blacklisted_candidates": blacklisted,
    }

    save_json_atomic(payload, CANDIDATES_V4_PATH)
    log.info(f"[scan_v4] 저장 → {CANDIDATES_V4_PATH}  "
             f"(tradable {len(tradable)} / blacklisted {len(blacklisted)})")

    # 히스토리 저장 — history/candidates_YYYY-MM-DD.json
    # ref_date 기준일을 그대로 사용 (실행일이 아닌 데이터 기준일).
    history_dir = BASE_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    if len(ref_date) == 8:
        history_name = f"candidates_{ref_date[:4]}-{ref_date[4:6]}-{ref_date[6:]}.json"
    else:
        history_name = f"candidates_{ref_date}.json"
    history_path = history_dir / history_name
    save_json_atomic(payload, history_path)
    log.info(f"[scan_v4] 히스토리 백업 → {history_path}")


# ============================================================
# scan — 후보 추출
# ============================================================
def run_scan(ref_date_override: str | None = None) -> None:
    if not DB_PATH.exists():
        log.error(f"DB 없음: {DB_PATH}")
        return

    with db_connect() as conn:
        if ref_date_override:
            ref_date = ref_date_override
        else:
            ref_date = conn.execute(
                "SELECT MAX(날짜) FROM daily_indicators"
            ).fetchone()[0]
        if not ref_date:
            log.error("daily_indicators 비어있음 — 먼저 --mode init 또는 --mode daily 실행")
            return

        log.info(f"[scan] 기준일 {ref_date}")

        filtered = set(load_filtered_tickers())
        if filtered:
            log.info(f"[scan] filtered_tickers.json — {len(filtered)}종목 대상")
        else:
            log.info("[scan] 필터 없음 — 전종목 대상")

        # OHLCV + indicator 조인 (최신일)
        cur = conn.execute(
            f"""
            SELECT d.종목코드, d.시장구분, d.종가, d.저가, d.거래량,
                   i.conv_std, i.base_std, i.span_a_std, i.span_b_std,
                   i.conv_2x, i.base_2x, i.span_a_2x, i.span_b_2x,
                   i.ma5, i.ma20, i.ma60, i.ma112, i.ma224, i.ma448,
                   i.bb_mid, i.bb_upper, i.bb_lower,
                   i.vol_ma20, i.vol_ratio,
                   m.종목명
            FROM daily_data d
            JOIN daily_indicators i
              ON d.날짜 = i.날짜 AND d.종목코드 = i.종목코드
            LEFT JOIN ticker_master m ON d.종목코드 = m.종목코드
            WHERE d.날짜 = ?
            """,
            (ref_date,),
        )
        columns = [c[0] for c in cur.description]
        rows = cur.fetchall()

    if not rows:
        log.warning(f"[scan] 기준일 {ref_date} 데이터 없음")
        save_json_atomic(
            {
                "date": ref_date,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "total_candidates": 0,
                "candidates": [],
                "note": "기준일 데이터 없음",
            },
            CANDIDATES_PATH,
        )
        return

    candidates: list[dict] = []
    for raw in rows:
        rec = dict(zip(columns, raw))
        ticker = rec["종목코드"]
        if filtered and ticker not in filtered:
            continue

        conds = evaluate_conditions(rec, CONFIG)
        score = score_conditions(conds, CONFIG["condition_weights"])
        if score <= 0:
            continue  # 0점은 의미 없음

        candidates.append(
            {
                "ticker": ticker,
                "name": rec.get("종목명") or "",
                "market": rec.get("시장구분") or "",
                "score": score,
                "close": int(rec["종가"]) if rec["종가"] is not None else None,
                "conditions": conds,
                "indicators_snapshot": {
                    "base_std": rec.get("base_std"),
                    "span_a_std": rec.get("span_a_std"),
                    "span_b_std": rec.get("span_b_std"),
                    "span_a_2x": rec.get("span_a_2x"),
                    "span_b_2x": rec.get("span_b_2x"),
                    "ma5": rec.get("ma5"),
                    "ma20": rec.get("ma20"),
                    "ma60": rec.get("ma60"),
                    "ma224": rec.get("ma224"),
                    "bb_lower": rec.get("bb_lower"),
                    "vol_ratio": rec.get("vol_ratio"),
                },
            }
        )

    candidates.sort(key=lambda x: (-x["score"], x["ticker"]))

    payload = {
        "date": ref_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_candidates": len(candidates),
        "config_snapshot": {
            "base_line_near_pct": CONFIG["base_line_near_pct"],
            "ma_convergence_pct": CONFIG["ma_convergence_pct"],
            "volume_surge_ratio": CONFIG["volume_surge_ratio"],
            "accumulation_bar_ratio": CONFIG["accumulation_bar_ratio"],
            "base_line_overheated_pct": CONFIG["base_line_overheated_pct"],
            "condition_weights": CONFIG["condition_weights"],
        },
        "filter_source": str(FILTER_PATH) if FILTER_PATH.exists() else None,
        "candidates": candidates,
    }
    save_json_atomic(payload, CANDIDATES_PATH)

    # 점수별 분포 요약
    dist: dict[int, int] = {}
    for c in candidates:
        dist[c["score"]] = dist.get(c["score"], 0) + 1
    log.info(f"[scan] 후보 {len(candidates)}종목 저장 → {CANDIDATES_PATH}")
    log.info(f"[scan] 점수 분포 {dict(sorted(dist.items(), reverse=True))}")


# ============================================================
# 진입점
# ============================================================
def _resolve_daily_from_date() -> str:
    """daily 모드에서 다시 계산할 시작일.

    지표의 윈도우 중 가장 긴 것(2배 span_b + shift = 104+52 = 156)보다 여유를 둔 시작일을
    '오늘에서 200 영업일 전' 대신 '오늘로부터 300 달력일 전'으로 잡아 안전하게 덮어쓴다.
    """
    days = 300
    from datetime import timedelta
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


def main() -> int:
    parser = argparse.ArgumentParser(description="단테 전략 - 지표 계산 + 후보 추출")
    parser.add_argument(
        "--mode",
        choices=["init", "daily", "scan", "scan_v4"],
        required=True,
        help="init=5년치 전체 / daily=최근 갱신 + scan / scan=후보(v1) / scan_v4=v4후보",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="scan 모드 기준일 YYYYMMDD (생략 시 daily_indicators 최신일)",
    )
    args = parser.parse_args()

    try:
        if args.mode == "init":
            run_compute("init", daily_from_date=None)
            run_scan(ref_date_override=None)
            run_scan_v4(ref_date_override=None)
        elif args.mode == "daily":
            run_compute("daily", daily_from_date=_resolve_daily_from_date())
            run_scan(ref_date_override=None)
            run_scan_v4(ref_date_override=None)
        elif args.mode == "scan_v4":
            run_scan_v4(ref_date_override=args.date)
        else:  # scan
            run_scan(ref_date_override=args.date)
    except KeyboardInterrupt:
        log.warning("사용자 중단 (Ctrl+C)")
        return 130
    except Exception as e:
        log.exception(f"치명적 오류: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
