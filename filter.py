"""국장 자동매매 시스템 - 2단계 필터 (filter.py)

stock_data.db에서 전종목을 불러와 매매 부적합 종목을 걸러낸다.

필터 정책
---------
A. pykrx 직접 지원 (1.2.7 기준 — 관리종목/경고/거래정지 전용 함수 없음)
   → pykrx 함수로는 해당 상태를 직접 조회할 수 없음을 기록만 남기고 스킵.
   → 거래정지는 아래 B-2 조건이 실질적으로 커버한다.

B. DB 기반 간접 필터
   1) 최근 30 영업일 내 daily_data 1건도 없음   → '데이터_없음'
   2) 최근 20 영업일 거래량이 한 번도 0 초과 없음 → '거래량_제로'
   3) 최근 거래일 종가가 999 이하                 → '저가_999원이하'

시가총액 필터는 사용하지 않는다 (단테 기법은 소형주 포함).

실행
----
- python filter.py                  : 최신 거래일 기준 필터링
- python filter.py --date 20260422  : 특정 거래일 기준 필터링

산출
----
C:\\Users\\sji48\\ksat_gang\\filtered_tickers.json
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
import json
import argparse
import logging
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler


# ============================================================
# 경로 / 상수
# ============================================================
BASE_DIR = Path(r"C:\Users\sji48\ksat_gang")
DB_PATH = BASE_DIR / "stock_data.db"
OUT_PATH = BASE_DIR / "filtered_tickers.json"
LOG_DIR = BASE_DIR / "logs"

BASE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

RECENT_DAYS_VOLUME = 20       # 거래량 0 판정 기간
RECENT_DAYS_EXISTENCE = 30    # 데이터 존재 판정 기간
MIN_CLOSE_PRICE = 1000        # 종가 1000원 미만 제외 (999원 이하 = 제외)

# 종목명 기반 제외 (부분 문자열 매칭, 대소문자 무관 아님 — 한글)
# "스팩" 한 글자라도 포함되면 전부 제외 — 스팩28호 / 스팩제7호 / 미래에셋스팩10호 등 모든 변형 커버
EXCLUDE_NAME_SUBSTRINGS = ["스팩"]


# ============================================================
# 로깅 (collector.py 와 동일 스타일)
# ============================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("filter")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = TimedRotatingFileHandler(
        LOG_DIR / "filter.log",
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
# DB 접속 (collector.py 와 동일 PRAGMA)
# ============================================================
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


# ============================================================
# pykrx 상태 기반 필터 (현재 버전 미지원 → 자리만 마련)
# ============================================================
def fetch_pykrx_status_sets(date_str: str) -> dict[str, set[str]]:
    """관리종목/투자경고·위험/거래정지 집합을 반환.

    pykrx 1.2.7 공개 API에는 해당 전용 엔드포인트가 없어 빈 집합을 반환한다.
    미래 버전에서 지원되면 이 함수만 교체하면 된다.
    """
    result = {"관리종목": set(), "투자경고위험": set(), "거래정지": set()}
    try:
        import inspect
        from pykrx import stock  # noqa: F401
        funcs = {n for n in dir(stock) if not n.startswith("_")}
        # 예시: 미래에 stock.get_market_caution_list 같은 엔드포인트가 생기면 여기서 감지
        candidates = {
            "관리종목": ["get_market_admin_issue_list", "get_admin_issue_list"],
            "투자경고위험": ["get_market_caution_list", "get_warning_issue_list"],
            "거래정지": ["get_market_halt_list", "get_halt_issue_list"],
        }
        for label, names in candidates.items():
            for n in names:
                if n in funcs:
                    try:
                        tickers = getattr(stock, n)(date_str)
                        result[label] = {str(t) for t in (tickers or [])}
                        log.info(f"pykrx {label} {n}() → {len(result[label])}건")
                        break
                    except Exception as e:
                        log.warning(f"pykrx {n} 호출 실패: {e}")
    except Exception as e:
        log.warning(f"pykrx 상태 조회 실패: {e}")

    if not any(result.values()):
        log.info(
            "pykrx 관리종목/투자경고·위험/거래정지 전용 함수는 현재 설치 버전(1.2.7)에서 미지원 — "
            "DB 기반 간접 필터만 적용 (거래정지는 '최근 20일 거래량 0' 조건이 커버)"
        )
    return result


# ============================================================
# DB 기반 기본 조회
# ============================================================
def latest_trading_date(conn: sqlite3.Connection, upper_bound: str | None = None) -> str | None:
    if upper_bound:
        row = conn.execute(
            "SELECT MAX(날짜) FROM daily_data WHERE 날짜 <= ?", (upper_bound,)
        ).fetchone()
    else:
        row = conn.execute("SELECT MAX(날짜) FROM daily_data").fetchone()
    return row[0] if row and row[0] else None


def recent_trading_dates(conn: sqlite3.Connection, ref_date: str, n_days: int) -> list[str]:
    """ref_date 이하의 최근 n_days 거래일 (내림차순)"""
    cur = conn.execute(
        """
        SELECT DISTINCT 날짜
        FROM daily_data
        WHERE 날짜 <= ?
        ORDER BY 날짜 DESC
        LIMIT ?
        """,
        (ref_date, n_days),
    )
    return [r[0] for r in cur.fetchall()]


def load_all_tickers(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(종목코드, 종목명, 시장구분) 리스트 — 정렬."""
    cur = conn.execute(
        "SELECT 종목코드, 종목명, 시장구분 FROM ticker_master ORDER BY 종목코드"
    )
    return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def load_recent_window(
    conn: sqlite3.Connection, date_from: str, date_to: str
) -> dict[str, list[tuple[str, int, int]]]:
    """종목코드 → [(날짜, 종가, 거래량), ...] (최신 우선).

    한 번의 쿼리로 전 종목 윈도우를 뽑아 파이썬 단에서 groupby 한다.
    """
    cur = conn.execute(
        """
        SELECT 종목코드, 날짜, 종가, 거래량
        FROM daily_data
        WHERE 날짜 BETWEEN ? AND ?
        """,
        (date_from, date_to),
    )
    out: dict[str, list[tuple[str, int, int]]] = {}
    for code, ds, close, vol in cur.fetchall():
        out.setdefault(code, []).append((ds, int(close or 0), int(vol or 0)))
    # 최신일 우선 정렬
    for rows in out.values():
        rows.sort(key=lambda x: x[0], reverse=True)
    return out


# ============================================================
# 필터링 엔진
# ============================================================
def apply_filters(
    tickers: list[tuple[str, str, str]],
    window: dict[str, list[tuple[str, int, int]]],
    ref_date: str,
    recent_volume_days: list[str],
    pykrx_status: dict[str, set[str]],
) -> tuple[list[str], dict[str, int], list[tuple[str, str]]]:
    """필터를 적용하고 (통과 종목코드, 사유별 카운트, 제외 상세) 반환.

    제외 순서(단일 사유로 집계 — 가장 먼저 매칭되는 사유로 분류):
    1) pykrx_관리종목
    2) pykrx_투자경고위험
    3) pykrx_거래정지
    4) 스팩_종목명 (종목명에 '스팩' 포함)
    5) 데이터_없음 (최근 30영업일 내 daily_data 0건)
    6) 거래량_제로 (최근 20영업일 거래량 합이 0)
    7) 저가_999원이하 (최근 거래일 종가 < 1000)
    """
    passed: list[str] = []
    excluded_count: dict[str, int] = {
        "pykrx_관리종목": 0,
        "pykrx_투자경고위험": 0,
        "pykrx_거래정지": 0,
        "스팩_종목명": 0,
        "데이터_없음": 0,
        "거래량_제로": 0,
        "저가_999원이하": 0,
    }
    excluded_detail: list[tuple[str, str]] = []  # (ticker, reason)

    vol_window = set(recent_volume_days)

    for code, name, _market in tickers:
        if code in pykrx_status["관리종목"]:
            excluded_count["pykrx_관리종목"] += 1
            excluded_detail.append((code, "pykrx_관리종목"))
            continue
        if code in pykrx_status["투자경고위험"]:
            excluded_count["pykrx_투자경고위험"] += 1
            excluded_detail.append((code, "pykrx_투자경고위험"))
            continue
        if code in pykrx_status["거래정지"]:
            excluded_count["pykrx_거래정지"] += 1
            excluded_detail.append((code, "pykrx_거래정지"))
            continue
        if name and any(kw in name for kw in EXCLUDE_NAME_SUBSTRINGS):
            excluded_count["스팩_종목명"] += 1
            excluded_detail.append((code, "스팩_종목명"))
            continue

        rows = window.get(code) or []
        if not rows:
            excluded_count["데이터_없음"] += 1
            excluded_detail.append((code, "데이터_없음"))
            continue

        recent_vol_rows = [r for r in rows if r[0] in vol_window]
        if not recent_vol_rows or sum(r[2] for r in recent_vol_rows) == 0:
            excluded_count["거래량_제로"] += 1
            excluded_detail.append((code, "거래량_제로"))
            continue

        latest_close = rows[0][1]
        if latest_close < MIN_CLOSE_PRICE:
            excluded_count["저가_999원이하"] += 1
            excluded_detail.append((code, "저가_999원이하"))
            continue

        passed.append(code)

    return passed, excluded_count, excluded_detail


# ============================================================
# 저장 (atomic)
# ============================================================
def save_json_atomic(payload: dict, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".filtered_", suffix=".tmp", dir=str(path.parent))
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


# ============================================================
# 메인
# ============================================================
def run(ref_date_override: str | None = None) -> int:
    if not DB_PATH.exists():
        log.error(f"DB 파일 없음: {DB_PATH}")
        return 1

    with db_connect() as conn:
        ref_date = latest_trading_date(conn, upper_bound=ref_date_override)
        if not ref_date:
            log.error("daily_data 가 비어있음 — collector.py 실행 필요")
            return 1

        tickers = load_all_tickers(conn)
        log.info(f"ticker_master 총 {len(tickers)}종목 / 기준일 {ref_date}")

        dates_existence = recent_trading_dates(conn, ref_date, RECENT_DAYS_EXISTENCE)
        dates_volume = recent_trading_dates(conn, ref_date, RECENT_DAYS_VOLUME)
        if len(dates_existence) < RECENT_DAYS_EXISTENCE:
            log.warning(
                f"최근 {RECENT_DAYS_EXISTENCE}영업일 모두 확보 실패 — "
                f"확보된 {len(dates_existence)}일로 진행"
            )

        date_from = min(dates_existence) if dates_existence else ref_date
        date_to = ref_date
        window = load_recent_window(conn, date_from, date_to)
        log.info(
            f"윈도우 {date_from} ~ {date_to}: "
            f"{len(window)}종목 데이터 로드"
        )

    pykrx_status = fetch_pykrx_status_sets(ref_date)

    passed, excluded_count, _detail = apply_filters(
        tickers, window, ref_date, dates_volume, pykrx_status
    )

    payload = {
        "date": ref_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tickers": len(tickers),
        "filtered_tickers": len(passed),
        "excluded_count": sum(excluded_count.values()),
        "excluded_reasons": excluded_count,
        "filter_config": {
            "recent_days_volume": RECENT_DAYS_VOLUME,
            "recent_days_existence": RECENT_DAYS_EXISTENCE,
            "min_close_price_won": MIN_CLOSE_PRICE,
            "market_cap_filter_enabled": False,
            "pykrx_version_supports_status": False,
            "pykrx_note": (
                "pykrx 1.2.7 공개 API에 관리종목/투자경고·위험/거래정지 전용 함수가 없음. "
                "거래정지는 '최근 20영업일 거래량 0' 조건으로 사실상 필터링됨."
            ),
        },
        "filtered_list": passed,
    }

    save_json_atomic(payload, OUT_PATH)
    log.info(
        f"저장 완료: {OUT_PATH} "
        f"(통과 {len(passed)}/{len(tickers)}, 제외 {sum(excluded_count.values())})"
    )
    log.info(f"제외 사유별: {excluded_count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="국장 자동매매 2단계 필터")
    parser.add_argument(
        "--date",
        default=None,
        help="기준일 YYYYMMDD (생략 시 DB 최신 거래일)",
    )
    args = parser.parse_args()

    try:
        return run(args.date)
    except KeyboardInterrupt:
        log.warning("사용자 중단 (Ctrl+C)")
        return 130
    except Exception as e:
        log.exception(f"치명적 오류: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
