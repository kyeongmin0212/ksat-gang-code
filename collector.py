"""국장 자동매매 시스템 - 1단계 데이터 수집기 (collector.py)

수집 대상
- KOSPI / KOSDAQ 일별 OHLCV (시가/고가/저가/종가/거래량/거래대금/시가총액)
- 종목 마스터 (종목코드/종목명/시장구분)

실행 모드
- python collector.py init     : 최초 5년치 수집
- python collector.py daily    : 일일 업데이트 (오후 7시 스케줄용)
- python collector.py resume   : 체크포인트부터 이어서 수집
"""
import os
os.environ["PYTHONUTF8"] = "1"

import sys
import json
import time
import gc
import logging
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

import pandas as pd
from pykrx import stock


# ============================================================
# 경로 / 상수
# ============================================================
BASE_DIR = Path(r"C:\Users\sji48\ksat_gang")
DB_PATH = BASE_DIR / "stock_data.db"
CHECKPOINT_PATH = BASE_DIR / "checkpoint.json"
LOG_DIR = BASE_DIR / "logs"

BASE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_YEARS = 5
MARKETS = ["KOSPI", "KOSDAQ"]
MAX_RETRY = 3
PYKRX_DELAY_SEC = 1.0           # pykrx 과부하 방지
TICKER_NAME_DELAY_SEC = 0.05
SESSION_TTL_SEC = 50 * 60       # KRX 1시간 만료 → 50분 주기 갱신


# ============================================================
# 로깅 (30일 로테이션)
# ============================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("collector")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = TimedRotatingFileHandler(
        LOG_DIR / "collector.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


log = setup_logging()


# ============================================================
# KRX 세션 관리 (1시간 만료 → 자동 재로그인)
# ============================================================
class KRXSession:
    def __init__(self):
        self.id = os.environ.get("KRX_ID")
        self.pw = os.environ.get("KRX_PW")
        self.last_login_ts: float | None = None
        if not self.id or not self.pw:
            log.warning("환경변수 KRX_ID/KRX_PW 미설정 - 익명 모드로 진행")

    def _login_once(self):
        # pykrx 자체에는 로그인 API가 없으므로
        # 더미 호출로 세션을 워밍업하고 타임스탬프만 갱신.
        today = datetime.now().strftime("%Y%m%d")
        stock.get_nearest_business_day_in_a_week(today)
        self.last_login_ts = time.time()

    def ensure(self):
        now = time.time()
        if self.last_login_ts is not None and (now - self.last_login_ts) <= SESSION_TTL_SEC:
            return
        last_err: Exception | None = None
        for attempt in range(1, MAX_RETRY + 1):
            try:
                self._login_once()
                log.info(f"KRX 세션 갱신 성공 (시도 {attempt})")
                return
            except Exception as e:
                last_err = e
                log.error(f"KRX 세션 갱신 실패 (시도 {attempt}/{MAX_RETRY}): {e}")
                time.sleep(2 ** attempt)
        raise RuntimeError(f"KRX 세션 {MAX_RETRY}회 연속 실패 - 다음날 재시도: {last_err}")


# ============================================================
# DB
# ============================================================
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def db_init():
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_data (
                날짜       TEXT NOT NULL,
                종목코드   TEXT NOT NULL,
                시장구분   TEXT NOT NULL,
                시가       INTEGER,
                고가       INTEGER,
                저가       INTEGER,
                종가       INTEGER,
                거래량     INTEGER,
                거래대금   INTEGER,
                시가총액   INTEGER,
                PRIMARY KEY (날짜, 종목코드)
            );

            CREATE INDEX IF NOT EXISTS idx_daily_ticker_date
                ON daily_data(종목코드, 날짜);
            CREATE INDEX IF NOT EXISTS idx_daily_market_date
                ON daily_data(시장구분, 날짜);

            CREATE TABLE IF NOT EXISTS ticker_master (
                종목코드        TEXT PRIMARY KEY,
                종목명          TEXT NOT NULL,
                시장구분        TEXT NOT NULL,
                최초등록일      TEXT NOT NULL,
                최종업데이트일  TEXT NOT NULL
            );
            """
        )
        conn.commit()
    log.info(f"DB 초기화 완료: {DB_PATH}")


def db_integrity_check() -> bool:
    with db_connect() as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        ok = bool(row) and row[0] == "ok"
    if ok:
        log.info("DB 무결성 OK")
    else:
        log.error(f"DB 무결성 실패: {row}")
    return ok


def upsert_daily(conn: sqlite3.Connection, rows: list[tuple]):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO daily_data
            (날짜, 종목코드, 시장구분, 시가, 고가, 저가, 종가, 거래량, 거래대금, 시가총액)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(날짜, 종목코드) DO UPDATE SET
            시장구분 = excluded.시장구분,
            시가     = excluded.시가,
            고가     = excluded.고가,
            저가     = excluded.저가,
            종가     = excluded.종가,
            거래량   = excluded.거래량,
            거래대금 = excluded.거래대금,
            시가총액 = excluded.시가총액
        """,
        rows,
    )


def upsert_ticker_master(conn: sqlite3.Connection, rows: list[tuple]):
    """rows: (종목코드, 종목명, 시장구분, 최초등록일, 최종업데이트일)"""
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO ticker_master
            (종목코드, 종목명, 시장구분, 최초등록일, 최종업데이트일)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(종목코드) DO UPDATE SET
            종목명         = excluded.종목명,
            시장구분       = excluded.시장구분,
            최종업데이트일 = excluded.최종업데이트일
        """,
        rows,
    )


def daily_count(conn: sqlite3.Connection, date_str: str, market: str | None = None) -> int:
    if market:
        cur = conn.execute(
            "SELECT COUNT(*) FROM daily_data WHERE 날짜=? AND 시장구분=?",
            (date_str, market),
        )
    else:
        cur = conn.execute(
            "SELECT COUNT(*) FROM daily_data WHERE 날짜=?", (date_str,)
        )
    return cur.fetchone()[0]


# ============================================================
# 체크포인트 (atomic 저장)
# ============================================================
DEFAULT_CHECKPOINT: dict = {
    "last_completed_date": None,
    "in_progress_date": None,
    "completed_markets_for_date": [],
    "failed_dates": [],
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


def save_checkpoint(cp: dict):
    """tempfile + os.replace 로 atomic 쓰기."""
    fd, tmp = tempfile.mkstemp(prefix=".checkpoint_", suffix=".tmp", dir=str(BASE_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cp, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CHECKPOINT_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ============================================================
# pykrx 호출 래퍼 (재시도)
# ============================================================
def _retry(func, *args, **kwargs):
    last: Exception | None = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last = e
            log.warning(f"{func.__name__} 실패 (시도 {attempt}/{MAX_RETRY}): {e}")
            time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"{func.__name__} 최종 실패: {last}") from last


def is_business_day(date_str: str) -> bool:
    try:
        nearest = _retry(stock.get_nearest_business_day_in_a_week, date_str)
        return nearest == date_str
    except Exception as e:
        log.error(f"거래일 확인 실패 {date_str}: {e}")
        return False


def fetch_ohlcv(date_str: str, market: str) -> pd.DataFrame:
    df = _retry(stock.get_market_ohlcv_by_ticker, date_str, market=market)
    time.sleep(PYKRX_DELAY_SEC)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.index.name = "종목코드"
    df.reset_index(inplace=True)
    df["시장구분"] = market
    df["날짜"] = date_str
    return df


def fetch_ticker_names(date_str: str, market: str) -> dict[str, str]:
    tickers = _retry(stock.get_market_ticker_list, date_str, market=market)
    time.sleep(PYKRX_DELAY_SEC)
    out: dict[str, str] = {}
    for t in tickers:
        try:
            name = _retry(stock.get_market_ticker_name, t)
            out[str(t)] = name
            time.sleep(TICKER_NAME_DELAY_SEC)
        except Exception as e:
            log.warning(f"종목명 조회 실패 {t}: {e}")
    return out


# ============================================================
# 일별 수집
# ============================================================
NUMERIC_COLS = ["시가", "고가", "저가", "종가", "거래량", "거래대금", "시가총액"]


def _df_to_rows(df: pd.DataFrame) -> list[tuple]:
    if df.empty:
        return []
    for col in NUMERIC_COLS:
        if col not in df.columns:
            df[col] = 0
    df = df.fillna(0)

    rows: list[tuple] = []
    for rec in df.to_dict("records"):
        rows.append(
            (
                rec["날짜"],
                str(rec["종목코드"]),
                rec["시장구분"],
                int(rec["시가"]),
                int(rec["고가"]),
                int(rec["저가"]),
                int(rec["종가"]),
                int(rec["거래량"]),
                int(rec["거래대금"]),
                int(rec["시가총액"]),
            )
        )
    return rows


def collect_one_date(
    krx: KRXSession,
    date_str: str,
    cp: dict,
    skip_markets: list[str] | None = None,
) -> bool:
    """하루치 KOSPI+KOSDAQ 수집. 성공 시 True."""
    skip_markets = skip_markets or []

    if not is_business_day(date_str):
        log.info(f"[{date_str}] 휴장일 - 스킵")
        # 휴장일도 체크포인트 갱신해서 다시 시도하지 않도록
        cp["last_completed_date"] = date_str
        cp["in_progress_date"] = None
        cp["completed_markets_for_date"] = []
        save_checkpoint(cp)
        return True

    cp["in_progress_date"] = date_str
    save_checkpoint(cp)

    for market in MARKETS:
        if market in skip_markets:
            log.info(f"[{date_str}/{market}] 체크포인트 - 스킵")
            continue

        try:
            krx.ensure()
            df = fetch_ohlcv(date_str, market)
        except Exception as e:
            log.error(f"[{date_str}/{market}] 수집 실패: {e}")
            return False

        if df.empty:
            log.warning(f"[{date_str}/{market}] 데이터 없음")
        else:
            rows = _df_to_rows(df)
            try:
                with db_connect() as conn:
                    upsert_daily(conn, rows)
                    conn.commit()
                log.info(f"[{date_str}/{market}] {len(rows)}건 저장")
            except Exception as e:
                log.error(f"[{date_str}/{market}] DB 저장 실패: {e}")
                return False
            finally:
                del rows

        completed = set(cp.get("completed_markets_for_date", []))
        completed.add(market)
        cp["completed_markets_for_date"] = sorted(completed)
        save_checkpoint(cp)

        del df
        gc.collect()

    # 완전성 체크
    with db_connect() as conn:
        n = daily_count(conn, date_str)
    if n == 0:
        log.error(f"[{date_str}] 완전성 체크 실패 - 0건")
        return False
    log.info(f"[{date_str}] 완전성 OK ({n}건)")

    cp["last_completed_date"] = date_str
    cp["in_progress_date"] = None
    cp["completed_markets_for_date"] = []
    if date_str in cp.get("failed_dates", []):
        cp["failed_dates"].remove(date_str)
    save_checkpoint(cp)
    return True


# ============================================================
# 종목 마스터 갱신 (상장폐지/신규상장 반영)
# ============================================================
def refresh_ticker_master(krx: KRXSession, date_str: str | None = None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
        date_str = _retry(stock.get_nearest_business_day_in_a_week, date_str)

    today = datetime.now().strftime("%Y-%m-%d")
    new_rows: list[tuple] = []

    for market in MARKETS:
        krx.ensure()
        try:
            names = fetch_ticker_names(date_str, market)
        except Exception as e:
            log.error(f"[ticker_master/{market}] 종목명 조회 실패: {e}")
            continue
        for code, name in names.items():
            new_rows.append((str(code), name, market, today, today))
        log.info(f"[ticker_master] {market} {len(names)}종목")

    if not new_rows:
        log.warning("[ticker_master] 갱신할 종목 없음")
        return

    with db_connect() as conn:
        existing = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT 종목코드, 최초등록일 FROM ticker_master"
            ).fetchall()
        }
        adjusted = [
            (code, name, market, existing.get(code, first), updated)
            for (code, name, market, first, updated) in new_rows
        ]
        upsert_ticker_master(conn, adjusted)
        conn.commit()
    log.info(f"[ticker_master] 총 {len(new_rows)}종목 갱신")


# ============================================================
# 모드별 실행
# ============================================================
def daterange(start: datetime, end: datetime):
    cur = start
    one = timedelta(days=1)
    while cur <= end:
        yield cur
        cur += one


def run_initial(years: int = INITIAL_YEARS):
    log.info(f"=== 최초 수집 시작 ({years}년) ===")
    db_init()
    krx = KRXSession()
    krx.ensure()

    cp = load_checkpoint()
    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=365 * years)

    if cp.get("last_completed_date"):
        try:
            resume = datetime.strptime(cp["last_completed_date"], "%Y%m%d") + timedelta(days=1)
            if resume > start:
                start = resume
                log.info(f"체크포인트 발견 - {start.strftime('%Y%m%d')} 부터 재개")
        except Exception:
            pass

    refresh_ticker_master(krx)

    for d in daterange(start, end):
        date_str = d.strftime("%Y%m%d")
        skip = (
            cp.get("completed_markets_for_date", [])
            if cp.get("in_progress_date") == date_str
            else []
        )
        ok = collect_one_date(krx, date_str, cp, skip_markets=skip)
        if not ok:
            log.error(f"[{date_str}] 수집 실패 - failed_dates 등록")
            failed = cp.setdefault("failed_dates", [])
            if date_str not in failed:
                failed.append(date_str)
                save_checkpoint(cp)

    db_integrity_check()
    log.info("=== 최초 수집 완료 ===")


def run_daily():
    log.info("=== 일일 업데이트 시작 ===")
    db_init()
    krx = KRXSession()
    krx.ensure()
    cp = load_checkpoint()

    today = datetime.now().strftime("%Y%m%d")

    # 1) 이전 실패 날짜 재시도
    for date_str in list(cp.get("failed_dates", [])):
        log.info(f"[재시도] {date_str}")
        if collect_one_date(krx, date_str, cp):
            if date_str in cp.get("failed_dates", []):
                cp["failed_dates"].remove(date_str)
                save_checkpoint(cp)

    # 2) 누락 날짜 보정 (마지막 완료일+1 ~ 어제)
    if cp.get("last_completed_date"):
        last = datetime.strptime(cp["last_completed_date"], "%Y%m%d")
        cursor = last + timedelta(days=1)
        end = datetime.now()
        while cursor < end:
            ds = cursor.strftime("%Y%m%d")
            if ds != today:
                log.info(f"[보정] {ds}")
                if not collect_one_date(krx, ds, cp):
                    failed = cp.setdefault("failed_dates", [])
                    if ds not in failed:
                        failed.append(ds)
                        save_checkpoint(cp)
            cursor += timedelta(days=1)

    # 3) 당일
    log.info(f"[당일] {today}")
    if not collect_one_date(krx, today, cp):
        failed = cp.setdefault("failed_dates", [])
        if today not in failed:
            failed.append(today)
            save_checkpoint(cp)

    # 4) 종목 마스터 갱신
    refresh_ticker_master(krx)

    db_integrity_check()
    log.info("=== 일일 업데이트 완료 ===")


def run_resume():
    log.info("=== 체크포인트 재개 ===")
    db_init()
    krx = KRXSession()
    krx.ensure()
    cp = load_checkpoint()

    if cp.get("in_progress_date"):
        ds = cp["in_progress_date"]
        skip = cp.get("completed_markets_for_date", [])
        log.info(f"[재개] {ds} (스킵: {skip})")
        collect_one_date(krx, ds, cp, skip_markets=skip)

    run_daily()


# ============================================================
# 진입점
# ============================================================
USAGE = "usage: python collector.py [init|daily|resume]"


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    mode = sys.argv[1].lower()
    try:
        if mode == "init":
            run_initial()
        elif mode == "daily":
            run_daily()
        elif mode == "resume":
            run_resume()
        else:
            print(USAGE)
            sys.exit(1)
    except KeyboardInterrupt:
        log.warning("사용자 중단 (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        log.exception(f"치명적 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
