"""v4 매수 추천 텔레그램 알림.

candidates_v4.json → 종목별 메시지 → 텔레그램 전송.

사용
----
python notifier.py                      : 즉시 발송
python notifier.py --candidates path    : 다른 파일 경로 지정
python notifier.py --dry-run            : 발송 없이 메시지만 출력 (테스트)

환경변수 TG_BOT_TOKEN, TG_CHAT_ID 가 있으면 우선 사용.
"""
from __future__ import annotations

import os
os.environ["PYTHONUTF8"] = "1"

import sys
# Windows 콘솔 UTF-8 출력 (이모지 포함)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import json
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime


# ============================================================
# 자격증명 — 환경변수 전용 (하드코딩 금지)
# ============================================================
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")


def require_credentials() -> None:
    """환경변수 미설정 시 안내 후 종료."""
    missing = []
    if not TG_BOT_TOKEN:
        missing.append("TG_BOT_TOKEN")
    if not TG_CHAT_ID:
        missing.append("TG_CHAT_ID")
    if not missing:
        return
    print("❌ 환경변수가 설정되지 않았습니다: " + ", ".join(missing))
    print("")
    print("설정 방법 (Windows PowerShell — 현재 세션에서만):")
    print('  $env:TG_BOT_TOKEN = "your_bot_token"')
    print('  $env:TG_CHAT_ID   = "your_chat_id"')
    print("")
    print("영구 설정 (재시작 후에도 유지):")
    print('  setx TG_BOT_TOKEN "your_bot_token"')
    print('  setx TG_CHAT_ID   "your_chat_id"')
    print("")
    print("Bash/cmd 일회성:")
    print("  set TG_BOT_TOKEN=your_bot_token  &&  set TG_CHAT_ID=your_chat_id  &&  python notifier.py")
    sys.exit(2)


BASE_DIR = Path(r"C:\Users\sji48\ksat_gang")
CANDIDATES_V4_PATH = BASE_DIR / "candidates_v4.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# API URL은 토큰 검증 후 lazy 구성
def _api_url() -> str:
    return f"https://api.telegram.org/bot{TG_BOT_TOKEN}"


# ============================================================
# 휴장일 / 데이터 미수집 감지
# ============================================================
# KRX 가 휴장이지만 holidays 라이브러리에는 없는 추가 휴장일 (월-일)
# · 5/1 근로자의 날 (공휴일은 아니지만 KRX 휴장)
# · 12/31 연말 폐장일
KRX_EXTRA_CLOSED_MMDD = {(5, 1), (12, 31)}


def determine_trading_day_skip(
    data: dict, today: datetime | None = None
) -> tuple[bool, str, str]:
    """오늘 알림 스킵 여부 + 데이터 신선도 경고.

    체크 순서:
      1) 토/일 → 스킵
      2) 한국 공휴일 (holidays.SouthKorea()) → 스킵
      3) KRX 추가 휴장일 (5/1, 12/31) → 스킵
      4) 평일 → 발송. 단 candidates 데이터가 3일 이상 오래되면 경고 텍스트 추가

    pykrx 영업일 체크는 사용 안 함 — 오전 8:30 실행 시 당일 KRX 데이터가 아직
    없어 어제로 fallback 되는 버그가 있음.

    Returns: (should_skip, skip_reason, stale_warning)
      · should_skip: True면 알림 전체 중단
      · skip_reason: should_skip=True 일 때 사유 문자열
      · stale_warning: should_skip=False 일 때 데이터 오래됨 경고 (있으면)
    """
    today = today or datetime.now()

    # 1) 주말
    if today.weekday() == 5:
        return True, "토요일 — KRX 시장 휴장", ""
    if today.weekday() == 6:
        return True, "일요일 — KRX 시장 휴장", ""

    # 2) 한국 공휴일
    try:
        import holidays as _hol
        kr = _hol.SouthKorea(years=today.year)
        if today.date() in kr:
            return True, f"한국 공휴일 ({kr.get(today.date())}) — KRX 휴장", ""
    except Exception as e:
        print(f"[notifier] holidays 체크 실패 ({e!s}) — 추가 휴장일만 검사")

    # 3) KRX 추가 휴장일
    if (today.month, today.day) in KRX_EXTRA_CLOSED_MMDD:
        if (today.month, today.day) == (5, 1):
            return True, "근로자의 날 — KRX 휴장", ""
        if (today.month, today.day) == (12, 31):
            return True, "연말 폐장일 — KRX 휴장", ""

    # 4) 데이터 신선도 — 평일이지만 candidates 가 3일 이상 오래됐으면 경고
    ref_date = (data or {}).get("date", "")
    stale_warning = ""
    if ref_date and len(ref_date) == 8:
        try:
            ref_dt = datetime.strptime(ref_date, "%Y%m%d")
            delta_days = (today - ref_dt).days
            if delta_days >= 3:
                ref_disp = f"{ref_date[:4]}-{ref_date[4:6]}-{ref_date[6:]}"
                stale_warning = (
                    f"⚠️ 데이터 오래됨 — candidates_v4.json 기준일 {ref_disp} "
                    f"({delta_days}일 전). 분석 파이프라인(KsatGang_Daily_Analysis) "
                    f"실행 여부 확인 필요."
                )
        except Exception:
            pass

    return False, "", stale_warning
SEND_DELAY_SEC = 0.4         # 메시지 사이 간격
TG_MAX_LEN = 4096            # 텔레그램 텍스트 길이 한도


# ============================================================
# 단테 차트 41장 분석 기반 분류 / 손절폭
#   logs/dante_chart_stoploss_analysis.txt + dante_classification_analysis.txt
# ============================================================
DANTE_CHART_STATS = {
    "스윙":        {"sl_pct": -6.24,  "n": 33, "target_range": "+15~30%"},
    "스윙&중장기": {"sl_pct": -8.92,  "n": 5,  "target_range": "+30~50%"},
    "중장기":      {"sl_pct": -12.97, "n": 3,  "target_range": "+50% 이상"},
}


def classify_by_target(recommended_pct: float) -> str:
    """단테 41장 기반 분류 — 추정 목표 % 로 결정.

    · 스윙        : < 30%
    · 스윙&중장기 : 30 ~ 50%
    · 중장기      : ≥ 50%
    """
    if recommended_pct < 30:
        return "스윙"
    if recommended_pct < 50:
        return "스윙&중장기"
    return "중장기"


def snap_to_tick(price: float) -> int:
    """KRX 호가 단위 스냅 (calculator.py / dante_strategy.py 와 동일)."""
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
# 텔레그램 API
# ============================================================
def tg_send(text: str, parse_mode: str | None = None) -> dict:
    """텔레그램 sendMessage API 호출. JSON 응답 반환."""
    if len(text) > TG_MAX_LEN:
        text = text[: TG_MAX_LEN - 20] + "\n…(잘림)"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_api_url()}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": str(e), "body": body}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tg_get_me() -> dict:
    """봇 인증 확인."""
    try:
        with urllib.request.urlopen(f"{_api_url()}/getMe", timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================
# 위험도 산정
# ============================================================
def assess_risk(stop_loss_pct: float | None, rr: float | None) -> str:
    """RR 기반 위험도 — 낮음/중간/높음.

    단테 손절폭은 분류별 표준이지 '위험'이 아니므로 RR(보상/위험 비율)로 판단:
      🟢 낮음: RR ≥ 5  (단테 정통 RR 1:5)
      🟡 중간: RR ≥ 3  (단테 최소 RR 1:3)
      🔴 높음: RR < 3
    """
    if rr is None:
        return "중간"
    if rr >= 5:
        return "낮음"
    if rr >= 3:
        return "중간"
    return "높음"


def cond_label(key: str) -> str | None:
    """단테 조건 키 → 한글 설명. 매수 메시지에 표시할 것만 라벨 부여."""
    return {
        "cloud_above_std": "일목 구름대 위 (강한 추세)",
        "cloud_above_2x": "단테 2배 구름대 위 (장기 강세)",
        "base_line_near": "일목 기준선 ±2% 근접 (눌림목 매수)",
        "ma_convergence": "MA5/20/60 수렴 (에너지 응축)",
        "volume_surge": "거래량 급증 — 세력 진입 신호",
        "accumulation_bar": "매집봉 — 강한 세력 매집",
        "bb_lower_touch": "볼린저 하단 터치 (저점 반등 자리)",
        "base_line_not_overheated": "기준선 이격 7% 미만 (과열 아님)",
    }.get(key)


# ============================================================
# 메시지 포맷
# ============================================================
def fmt_intro(data: dict) -> str:
    market = data.get("market_state", {})
    summ = data.get("filter_summary", {})
    bt = data.get("backtest_reference", {})
    date = data.get("date", "")
    date_disp = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) >= 8 else date
    bull = market.get("is_bull", True)

    lines = []
    lines.append("📈 단테 v4 전략 매수 추천")
    lines.append(f"📅 기준일: {date_disp}")
    if bull:
        lines.append("🌞 시장: 강세장 (KOSPI ≥ 200MA) — 매수 가능")
    else:
        lines.append("⚠️ 시장: 약세장 (KOSPI < 200MA) — 매수 중단")
    lines.append("")
    lines.append(
        f"📊 후보 {summ.get('tradable_candidates', 0)}종목  "
        f"(블랙리스트 {summ.get('blacklisted_count', 0)})"
    )
    lines.append("")
    lines.append("📚 v4 백테스트 (2021~2026, 5년)")
    lines.append(
        f"   CAGR {bt.get('cagr_pct', 0)}%  /  Sharpe {bt.get('sharpe', 0)}  /  "
        f"MDD {bt.get('mdd_pct', 0)}%  /  승률 {bt.get('win_rate_pct', 0)}%"
    )
    lines.append("⚠️ 백테스트는 낙관적 — 실전 예상 CAGR 25~35%")
    return "\n".join(lines)


def fmt_bear_warning(data: dict) -> str:
    market = data.get("market_state", {})
    today = market.get("kospi_proxy_today", 0)
    ma200 = market.get("kospi_proxy_200ma", 0)
    pct = ((today - ma200) / ma200 * 100) if ma200 else 0
    lines = [
        "⚠️ 오늘은 약세장입니다",
        "",
        f"KOSPI 시총 proxy: {today:,.0f}",
        f"200MA: {ma200:,.0f}  ({pct:+.2f}%)",
        "",
        "🛑 신규 매수 중단",
        "📉 기존 보유 종목은 계획대로 매도",
        "🛟 현금 100% 유지 검토",
        "",
        "다음 강세장 진입까지 관망 — 매일 자동 체크 중",
    ]
    return "\n".join(lines)


def fmt_candidate(c: dict, idx: int, total: int) -> str:
    bs = c.get("buy_stages", {})
    close = c.get("close", 0)
    target = c.get("target_median", 0)
    rec_pct = c.get("recommended_pct", 0)

    # 단테 41장 기반 분류 (목표 % 로 결정)
    classification = classify_by_target(rec_pct)
    spec = DANTE_CHART_STATS[classification]
    dante_sl_pct = spec["sl_pct"]                # 단테 평균 (예: -6.24)
    target_range = spec["target_range"]          # "+15~30%" 등

    # 손절가 = 매수가(현재가) × (1 + 단테 손절률)
    new_sl_price = snap_to_tick(close * (1 + dante_sl_pct / 100))
    actual_sl_pct = ((new_sl_price - close) / close * 100) if close else 0

    # RR 재계산 — 새 손절가 기준
    risk = max(close - new_sl_price, 0)
    rr = round((target - close) / risk, 2) if risk > 0 else None

    risk_level = assess_risk(actual_sl_pct, rr)
    risk_emoji = {"낮음": "🟢", "중간": "🟡", "높음": "🔴"}.get(risk_level, "🟡")

    lines = []
    lines.append(f"📌 [{idx}/{total}] {c.get('name', '')} ({c.get('ticker', '')})")
    lines.append(f"💰 현재가: {close:,}원")
    lines.append(f"📊 분류: {classification} (목표 {target_range})")
    lines.append("")
    stage_prices = [s["price"] for s in bs.values() if "price" in s]
    if stage_prices:
        lines.append("📊 분할매수 영역")
        lines.append(f"   {min(stage_prices):,}원 ~ {max(stage_prices):,}원")
    lines.append("")
    lines.append("🛑 손절가")
    lines.append(f"   {new_sl_price:,}원")
    lines.append("")
    lines.append(f"🎯 목표가: {target:,}원 ({rec_pct:+.2f}%)")
    if rr is not None:
        lines.append(f"📈 RR: {rr:.2f}")
    lines.append("")

    # 단테 조건
    conds = c.get("conditions", {})
    lines.append("💡 선택 이유 (단테 기법)")
    lines.append(f"   • 점수 4점 (단테 진입 최적 타이밍)")
    for key, val in conds.items():
        if not val:
            continue
        lbl = cond_label(key)
        if lbl:
            lines.append(f"   • {lbl}")
    lines.append("")
    lines.append(f"{risk_emoji} 위험도: {risk_level}")

    if c.get("blacklisted"):
        lines.append("")
        lines.append("⚠️ 블랙리스트 종목 — " + (c.get("blacklist_reason") or ""))

    if c.get("recently_sold"):
        lines.append(f"⚠️ 최근 매도 이력: {c['recently_sold']}")

    return "\n".join(lines)


# ============================================================
# 메인
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="단테 v4 텔레그램 알림")
    parser.add_argument("--candidates", default=str(CANDIDATES_V4_PATH),
                        help="candidates_v4.json 경로")
    parser.add_argument("--dry-run", action="store_true",
                        help="텔레그램 발송 없이 메시지만 출력")
    parser.add_argument("--limit", type=int, default=0,
                        help="발송 종목 수 제한 (0=전체)")
    parser.add_argument("--force", action="store_true",
                        help="휴장일 스킵 무시하고 강제 발송 (테스트용)")
    args = parser.parse_args()

    cand_path = Path(args.candidates)
    if not cand_path.exists():
        print(f"❌ candidates 파일 없음: {cand_path}")
        return 1

    data = json.loads(cand_path.read_text(encoding="utf-8"))

    # 휴장일 / 데이터 신선도 검증
    #   · skip=True   → 알림 전체 중단
    #   · stale_warn  → 발송하되 인트로 위에 경고 prepend
    stale_warning = ""
    if not args.force:
        skip, reason, stale_warning = determine_trading_day_skip(data)
        if skip:
            print(f"[notifier] SKIP — {reason}")
            return 0
        if stale_warning:
            print(f"[notifier] {stale_warning}")

    # 환경변수 검증 — dry-run 이 아닐 때만 필수 (실 발송 시점에만)
    if not args.dry_run:
        require_credentials()
    market = data.get("market_state", {})
    bear = market.get("bear_market", False)
    tradable = data.get("tradable_candidates", []) or []
    blacklisted = data.get("blacklisted_candidates", []) or []

    print(f"[notifier] 후보 로드: tradable={len(tradable)}, blacklisted={len(blacklisted)}, "
          f"bear={bear}")

    # 봇 인증 확인 (dry-run이 아닐 때만)
    if not args.dry_run:
        me = tg_get_me()
        if not me.get("ok"):
            print(f"❌ 봇 인증 실패: {me}")
            return 1
        bot_name = me.get("result", {}).get("username")
        print(f"[notifier] 봇 OK — @{bot_name}")

    # 1. 인트로 (+ 데이터 오래됨 경고 prepend)
    msgs: list[str] = []
    if stale_warning:
        msgs.append(stale_warning)
    msgs.append(fmt_intro(data))

    # 2. 약세장이면 경고만 발송
    if bear:
        msgs.append(fmt_bear_warning(data))
    else:
        if not tradable:
            msgs.append("ℹ️ 오늘 v4 조건을 만족하는 매수 후보가 없습니다.")
        else:
            picked = tradable if args.limit <= 0 else tradable[: args.limit]
            for i, c in enumerate(picked, 1):
                msgs.append(fmt_candidate(c, i, len(picked)))
            # 블랙리스트도 별도 안내
            if blacklisted:
                bl_lines = ["⚠️ 블랙리스트 등재 (참고용 — 매수 비권장)"]
                for c in blacklisted:
                    bl_lines.append(
                        f"  • {c.get('ticker', '')} {c.get('name', '')}  "
                        f"(목표 {c.get('recommended_pct', 0):+.2f}%)"
                    )
                msgs.append("\n".join(bl_lines))

    # 3. 발송
    if args.dry_run:
        print("=" * 60)
        for i, m in enumerate(msgs, 1):
            print(f"[메시지 #{i}] ({len(m)}자)")
            print(m)
            print("-" * 60)
        return 0

    sent_ok = 0
    sent_fail = 0
    for i, m in enumerate(msgs, 1):
        res = tg_send(m)
        if res.get("ok"):
            sent_ok += 1
            print(f"[{i}/{len(msgs)}] ✅ 발송 성공 ({len(m)}자)")
        else:
            sent_fail += 1
            print(f"[{i}/{len(msgs)}] ❌ 실패: {res.get('error')} {res.get('body', '')[:200]}")
        time.sleep(SEND_DELAY_SEC)

    print(f"[notifier] 완료 — 성공 {sent_ok} / 실패 {sent_fail}")
    return 0 if sent_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
