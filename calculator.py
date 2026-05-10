"""국장 자동매매 시스템 - 4단계 가격 계산기 (calculator.py)

입력
----
- candidates.json : 3단계 dante_strategy.py 의 후보 리스트
- stock_data.db   : 60일 고저 / 120일 전고점 조회용

출력
----
- candidates_with_prices.json : 종목별 매수/손절/목표/포지션 타입 계산 결과

계산 규칙 (dante_rules_v2.json 기반)
----------------------------------
1) 매수가 (분할매수 1 : 2 : 4 : 8)
   - 1차: 현재가                (자금 1)
   - 2차: 현재가 × 0.97         (자금 2, -3%)
   - 3차: 현재가 × 0.94         (자금 4, -6%)
   - 4차: 현재가 × 0.91         (자금 8, -9%)

2) 손절가 (이중 손절)
   - 1차: 일목 기준선(base_std) × 0.985   (기준선 -1.5%)     → 반매도
   - 2차: 선행스팬2(span_b_std)           (구름 하단)         → 전량청산

3) 목표가 3가지
   A. 파동 에너지 : 현재가 + (최근 60일 고점 - 저점) × 1.5
   B. 전 고점     : 최근 120일 고점
   C. RR 1:5      : 현재가 + (현재가 - 1차 손절가) × 5
   → 3개 중 '중간값'(median)을 기본 추천 목표로 사용

4) 포지션 타입 분류 (추천 목표 수익률 기준)
   - 스윙          : 수익률 ≤ 15%  (수일 ~ 3주)
   - 스윙_중장기   : 15% < x ≤ 30% (3주 ~ 2개월)
   - 중장기        : 30% 초과      (수주 ~ 수개월)

5) 경고 (제외 아님)
   - 추천 수익률 ≤ 15% → warning="진입 비권장 — 목표 ≤15%"

6) 부분 익절 알림
   - partial_exit_80 = 현재가 + (추천목표 - 현재가) × 0.8

모든 가격은 KRX 호가 단위로 스냅된다 (snap_to_tick).
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


# ============================================================
# 경로 / 상수
# ============================================================
BASE_DIR = Path(r"C:\Users\sji48\ksat_gang")
DB_PATH = BASE_DIR / "stock_data.db"
CANDIDATES_PATH = BASE_DIR / "candidates.json"
OUT_PATH = BASE_DIR / "candidates_with_prices.json"
CHECKPOINT_PATH = BASE_DIR / "calculator_checkpoint.json"
LOG_DIR = BASE_DIR / "logs"

BASE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 설정 (전부 CONFIG 에서 조정 가능)
# ============================================================
CONFIG: dict = {
    # 분할매수
    "split_buy_pct_offsets": [0.00, -0.03, -0.06, -0.09],
    "split_buy_fund_ratios": [1, 2, 4, 8],

    # 손절
    "stop_loss_1_base_deviation_pct": -0.015,  # 기준선 -1.5%

    # 목표가
    "wave_lookback_days": 60,
    "wave_energy_multiplier": 1.5,
    "prior_high_lookback_days": 120,
    "rr_multiplier": 5,

    # 기타
    "partial_exit_ratio": 0.8,
    "position_type_thresholds": {
        "swing_max_pct": 15.0,
        "mid_max_pct": 30.0,
    },
    "warning_below_pct": 15.0,

    # 진행 로그
    "log_progress_every": 200,
}


# ============================================================
# 로깅
# ============================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("calculator")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = TimedRotatingFileHandler(
        LOG_DIR / "calculator.log",
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
# 유틸
# ============================================================
def snap_to_tick(price: float) -> int:
    """KRX 호가 단위로 반올림하여 정수 가격 반환.

    KRX 호가 단위
    - <1,000원       : 1원
    - <5,000원       : 5원
    - <10,000원      : 10원
    - <50,000원      : 50원
    - <100,000원     : 100원
    - <500,000원     : 500원
    - ≥500,000원    : 1,000원
    """
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


def pct_change(base: float, to: float) -> float:
    """(to-base)/base * 100, 소수 2자리"""
    if base is None or base == 0:
        return 0.0
    return round((to - base) / base * 100, 2)


# ============================================================
# DB
# ============================================================
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def fetch_recent_window(
    conn: sqlite3.Connection, ticker: str, ref_date: str, days: int
) -> list[tuple[str, int, int, int]]:
    """(날짜, 고가, 저가, 종가) 최근 days 영업일 내림차순."""
    cur = conn.execute(
        """
        SELECT 날짜, 고가, 저가, 종가
        FROM daily_data
        WHERE 종목코드 = ? AND 날짜 <= ?
        ORDER BY 날짜 DESC
        LIMIT ?
        """,
        (ticker, ref_date, days),
    )
    return cur.fetchall()


# ============================================================
# 체크포인트
# ============================================================
DEFAULT_CHECKPOINT: dict = {
    "started_at": None,
    "updated_at": None,
    "ref_date": None,
    "total_candidates": 0,
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
# 핵심 계산
# ============================================================
def calculate_for_candidate(
    cand: dict,
    ohlcv_window: list[tuple[str, int, int, int]],
    cfg: dict,
) -> tuple[dict | None, str | None]:
    """단일 후보의 가격 계산.

    Returns
    -------
    (result, skip_reason)
        - (dict, None) : 정상 계산 결과
        - (None, "사유") : 스킵 사유 (실패 카운터에 반영)
    """
    current_price = cand.get("close")
    snap = cand.get("indicators_snapshot") or {}
    base_std = snap.get("base_std")
    span_b_std = snap.get("span_b_std")

    if not current_price or not base_std or not span_b_std:
        return None, "지표_누락"

    # --- 매수가 (분할매수) ---
    buy_prices: dict[str, dict] = {}
    for i, (offset, ratio) in enumerate(
        zip(cfg["split_buy_pct_offsets"], cfg["split_buy_fund_ratios"]), start=1
    ):
        raw = current_price * (1.0 + offset)
        buy_prices[f"{i}차"] = {
            "price": snap_to_tick(raw),
            "fund_ratio": ratio,
        }

    # --- 손절가 (이중) ---
    sl1_raw = base_std * (1.0 + cfg["stop_loss_1_base_deviation_pct"])
    sl1 = snap_to_tick(sl1_raw)
    sl2 = snap_to_tick(span_b_std)

    stop_loss = {
        "1차": {"price": sl1, "percent": pct_change(current_price, sl1), "action": "반매도"},
        "2차": {"price": sl2, "percent": pct_change(current_price, sl2), "action": "전량청산"},
    }

    # --- 진입 가능 여부 판단 (블로킹 사유 수집) ---
    # spec: 1차 손절(기준선 -1.5%)이 현재가 이상이면 진입 불가로 마킹 (제외 X, 유지 O)
    entry_possible = True
    entry_status = "정상"
    entry_reason: str | None = None
    entry_block_code: str | None = None  # summary 집계용

    if sl1 >= current_price:
        entry_possible = False
        entry_status = "현재 진입 불가"
        entry_reason = "1차 손절이 현재가 위 (기준선 아래 위치)"
        entry_block_code = "손절_현재가_위"

    # --- 목표가 3종 ---
    # ohlcv_window: 내림차순 [(날짜, 고가, 저가, 종가), ...]
    if not ohlcv_window:
        return None, "OHLCV_없음"
    wave_rows = ohlcv_window[: cfg["wave_lookback_days"]]
    prior_rows = ohlcv_window[: cfg["prior_high_lookback_days"]]

    wave_high = max(r[1] for r in wave_rows) if wave_rows else 0
    wave_low = min(r[2] for r in wave_rows) if wave_rows else 0
    prior_high = max(r[1] for r in prior_rows) if prior_rows else 0

    wave_range = max(wave_high - wave_low, 0)
    target_a_raw = current_price + wave_range * cfg["wave_energy_multiplier"]
    target_a = snap_to_tick(target_a_raw)

    target_b = snap_to_tick(prior_high) if prior_high > 0 else 0

    risk_per_share = max(current_price - sl1, 0)
    target_c_raw = current_price + risk_per_share * cfg["rr_multiplier"]
    target_c = snap_to_tick(target_c_raw)

    target_options = {
        "파동에너지": {"price": target_a, "percent": pct_change(current_price, target_a)},
        "전고점":     {"price": target_b, "percent": pct_change(current_price, target_b)},
        "RR_1대5":    {"price": target_c, "percent": pct_change(current_price, target_c)},
    }

    # --- 추천 = 중간값 (가격 기준) ---
    triples = [
        (target_a, "파동에너지"),
        (target_b, "전고점"),
        (target_c, "RR_1대5"),
    ]
    triples.sort(key=lambda x: x[0])
    recommended_price, recommended_method = triples[1]
    recommended_pct = pct_change(current_price, recommended_price)

    recommended_target = {
        "price": recommended_price,
        "percent": recommended_pct,
        "method": recommended_method,
    }

    # --- 포지션 타입 + 보유 기간 ---
    thr = cfg["position_type_thresholds"]
    if recommended_pct <= thr["swing_max_pct"]:
        position_type = "스윙"
        holding_period = "수일 ~ 3주"
    elif recommended_pct <= thr["mid_max_pct"]:
        position_type = "스윙_중장기"
        holding_period = "3주 ~ 2개월"
    else:
        position_type = "중장기"
        holding_period = "수주 ~ 수개월"

    # --- 경고 / 재검토 조건 ---
    # - warning: 진입 가능하지만 목표가 낮은 경우의 경고 (entry_possible=True 전용)
    # - review_condition: 진입 불가 종목이 다시 가능해지려면 필요한 조건
    warning: str | None = None
    review_condition: str | None = None
    if not entry_possible:
        if entry_block_code == "손절_현재가_위":
            review_condition = "기준선이 현재가 이하로 내려오면 재검토"
        else:
            review_condition = "진입 조건 재충족 시 재검토"
    elif recommended_pct <= cfg["warning_below_pct"]:
        warning = f"진입 비권장 — 추천 목표 {recommended_pct}% ≤ {cfg['warning_below_pct']}%"

    # --- 부분 익절 80% ---
    partial_raw = current_price + (recommended_price - current_price) * cfg["partial_exit_ratio"]
    partial_exit_80 = snap_to_tick(partial_raw)

    # --- RR 비율 (추천 목표 기준) ---
    if risk_per_share > 0:
        reward = recommended_price - current_price
        rr_ratio = round(reward / risk_per_share, 2) if reward > 0 else 0.0
    else:
        rr_ratio = None

    return (
        {
            "buy_prices": buy_prices,
            "stop_loss": stop_loss,
            "target_options": target_options,
            "recommended_target": recommended_target,
            "position_type": position_type,
            "holding_period": holding_period,
            "entry_possible": entry_possible,
            "entry_status": entry_status,
            "entry_reason": entry_reason,
            "entry_block_code": entry_block_code,
            "review_condition": review_condition,
            "warning": warning,
            "partial_exit_80": partial_exit_80,
            "risk_reward_ratio": rr_ratio,
        },
        None,
    )


# ============================================================
# 메인 루프
# ============================================================
def run() -> int:
    if not CANDIDATES_PATH.exists():
        log.error(f"candidates.json 없음: {CANDIDATES_PATH}")
        return 1

    src = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    ref_date = src.get("date")
    all_cands = src.get("candidates", [])
    log.info(f"candidates.json 로드 — 기준일 {ref_date}, 후보 {len(all_cands)}종목")

    cp = load_checkpoint()
    if cp.get("ref_date") != ref_date:
        # 새 기준일 → 체크포인트 리셋
        cp = dict(DEFAULT_CHECKPOINT)
        cp["ref_date"] = ref_date
        cp["started_at"] = datetime.now().isoformat(timespec="seconds")
        cp["total_candidates"] = len(all_cands)
        save_checkpoint(cp)
        log.info("새 세션 시작 (체크포인트 초기화)")
    else:
        log.info(
            f"체크포인트 재개 — 이미 완료 {len(cp['completed_tickers'])}, "
            f"실패 {len(cp['failed_tickers'])}"
        )

    completed = set(cp["completed_tickers"])
    failed = set(cp["failed_tickers"])
    skip_reasons: dict[str, int] = {}  # 제외 사유별 카운터

    # 이미 부분 저장된 결과가 있으면 읽어와서 이어쓰기
    prior_results_by_ticker: dict[str, dict] = {}
    if OUT_PATH.exists() and completed:
        try:
            prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            if prev.get("date") == ref_date:
                for e in prev.get("candidates", []):
                    prior_results_by_ticker[e["ticker"]] = e
        except Exception as e:
            log.warning(f"이전 결과 파일 읽기 실패 - 무시: {e}")

    results: list[dict] = []
    t_start = time.time()
    processed = 0

    lookback = max(CONFIG["wave_lookback_days"], CONFIG["prior_high_lookback_days"])

    for idx, cand in enumerate(all_cands, start=1):
        ticker = cand["ticker"]

        if ticker in completed and ticker in prior_results_by_ticker:
            results.append(prior_results_by_ticker[ticker])
            continue

        try:
            with db_connect() as conn:
                window = fetch_recent_window(conn, ticker, ref_date, lookback)

            calc, skip_reason = calculate_for_candidate(cand, window, CONFIG)
            if calc is None:
                reason = skip_reason or "미상"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                log.debug(f"[{ticker}] 스킵 ({reason})")
                failed.add(ticker)
                continue

            # 원본 필드 + 계산 결과 병합
            out_rec = {
                "ticker": ticker,
                "name": cand.get("name", ""),
                "market": cand.get("market", ""),
                "score": cand.get("score"),
                "current_price": cand.get("close"),
                "entry_possible": calc["entry_possible"],
                "entry_status": calc["entry_status"],
                "entry_reason": calc["entry_reason"],
                "review_condition": calc["review_condition"],
                "position_type": calc["position_type"],
                "holding_period": calc["holding_period"],
                "warning": calc["warning"],
                "buy_prices": calc["buy_prices"],
                "stop_loss": calc["stop_loss"],
                "target_options": calc["target_options"],
                "recommended_target": calc["recommended_target"],
                "partial_exit_80": calc["partial_exit_80"],
                "risk_reward_ratio": calc["risk_reward_ratio"],
                "conditions": cand.get("conditions", {}),
                "_block_code": calc["entry_block_code"],  # 내부 — summary 집계 후 제거
            }
            results.append(out_rec)
            completed.add(ticker)
            processed += 1

            if processed % CONFIG["log_progress_every"] == 0:
                elapsed = time.time() - t_start
                rate = processed / elapsed if elapsed > 0 else 0
                remain = len(all_cands) - len(completed)
                eta = remain / rate if rate > 0 else 0
                log.info(
                    f"진행 {len(completed)}/{len(all_cands)} "
                    f"({100*len(completed)/len(all_cands):.1f}%) "
                    f"rate={rate:.1f}/s eta={eta:.0f}s failed={len(failed)}"
                )
                cp["completed_tickers"] = sorted(completed)
                cp["failed_tickers"] = sorted(failed)
                save_checkpoint(cp)

            if processed % 500 == 0:
                gc.collect()

        except Exception as e:
            log.error(f"[{ticker}] 계산 실패 — 스킵: {e}")
            failed.add(ticker)
            continue

    # 점수 높은 순 정렬 (동일 점수는 ticker 오름차순)
    results.sort(key=lambda x: (-(x.get("score") or 0), x["ticker"]))

    # 요약 카운트 (진입 가능한 종목 기준으로 position_types 집계)
    n_possible = 0
    n_blocked = 0
    blocked_reasons: dict[str, int] = {}
    n_warning = 0
    position_types: dict[str, int] = {"스윙": 0, "스윙_중장기": 0, "중장기": 0}
    entry_possible_tickers: list[str] = []

    for r in results:
        if r["entry_possible"]:
            n_possible += 1
            entry_possible_tickers.append(r["ticker"])
            pt = r["position_type"]
            if pt in position_types:
                position_types[pt] += 1
        else:
            n_blocked += 1
            code = r.get("_block_code") or "미상"
            blocked_reasons[code] = blocked_reasons.get(code, 0) + 1
        if r["warning"]:
            n_warning += 1

    # 내부 필드 제거 (summary 집계 후)
    for r in results:
        r.pop("_block_code", None)

    summary = {
        "entry_possible": n_possible,
        "entry_blocked": n_blocked,
        "blocked_reasons": blocked_reasons,
        "position_types": position_types,
        "warning_count": n_warning,
        "failed_compute": len(failed),
        "failed_reasons": skip_reasons,
    }

    payload = {
        "date": ref_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_candidates": len(results),
        "summary": summary,
        "entry_possible_tickers": entry_possible_tickers,
        "config_snapshot": {
            "split_buy_pct_offsets": CONFIG["split_buy_pct_offsets"],
            "split_buy_fund_ratios": CONFIG["split_buy_fund_ratios"],
            "stop_loss_1_base_deviation_pct": CONFIG["stop_loss_1_base_deviation_pct"],
            "wave_lookback_days": CONFIG["wave_lookback_days"],
            "wave_energy_multiplier": CONFIG["wave_energy_multiplier"],
            "prior_high_lookback_days": CONFIG["prior_high_lookback_days"],
            "rr_multiplier": CONFIG["rr_multiplier"],
            "partial_exit_ratio": CONFIG["partial_exit_ratio"],
            "position_type_thresholds": CONFIG["position_type_thresholds"],
            "warning_below_pct": CONFIG["warning_below_pct"],
        },
        "candidates": results,
    }
    save_json_atomic(payload, OUT_PATH)

    cp["completed_tickers"] = sorted(completed)
    cp["failed_tickers"] = sorted(failed)
    save_checkpoint(cp)

    elapsed = time.time() - t_start
    log.info(
        f"완료 — 총 {len(all_cands)} / 저장 {len(results)} "
        f"(진입가능 {n_possible}, 진입불가 {n_blocked}) / "
        f"계산실패 {len(failed)} / {elapsed:.1f}s → {OUT_PATH}"
    )
    if blocked_reasons:
        log.info(f"진입 불가 사유별: {blocked_reasons}")
    if skip_reasons:
        log.info(f"계산실패 사유별: {skip_reasons}")
    log.info(f"요약: {summary}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="단테 전략 - 매수/손절/목표 가격 계산")
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
