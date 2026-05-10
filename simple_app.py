"""단테 v4 매수 추천 히스토리 — 단순 페이지네이션 뷰어.

한 페이지 = 하루치 매수 추천. 페이지 1 = 최신, 페이지 N = 가장 옛날.

데이터 소스
-----------
  · ksat_gang/history/candidates_*.json  (분석 파이프라인이 매일 19:00 백업)
  · ksat_gang/candidates_v4.json         (현재 — history 에 없을 때만)

실행
----
  cd C:\\Users\\sji48\\ksat_gang
  streamlit run simple_app.py

※ dashboard/ 밖에 둔 이유: dashboard/pages/ 자동 인식으로 사이드바에
   불필요한 페이지 메뉴(history/performance/detail)가 뜨는 것을 회피.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, date

import streamlit as st

# notifier.py 의 계산 로직을 재사용 (수정 금지 — import 만)
ROOT = Path(__file__).resolve().parent
from notifier import (
    classify_by_target,
    snap_to_tick,
    assess_risk,
    cond_label,
    DANTE_CHART_STATS,
)

CURRENT_PATH = ROOT / "candidates_v4.json"
HISTORY_DIR = ROOT / "history"
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def parse_date_str(s: str) -> date | None:
    if not s or len(s) < 8:
        return None
    try:
        return datetime.strptime(s[:8], "%Y%m%d").date()
    except ValueError:
        return None


def fmt_date_disp(date_str: str) -> str:
    d = parse_date_str(date_str)
    if not d:
        return date_str
    return f"{d.strftime('%Y-%m-%d')} ({WEEKDAY_KR[d.weekday()]})"


def fmt_date_short(date_str: str) -> str:
    """4-30 형식 (캡션용)."""
    d = parse_date_str(date_str)
    if not d:
        return date_str
    return d.strftime("%-m-%d") if hasattr(d, "strftime") else date_str


@st.cache_data(ttl=300, show_spinner=False)
def load_all_candidates() -> list[dict]:
    """history/ + 현재 파일 모두 로드, date 필드 기준 내림차순.

    중복 시 history 우선 (atomic write 로 안정적).
    """
    by_date: dict[str, dict] = {}

    if HISTORY_DIR.exists():
        for p in sorted(HISTORY_DIR.glob("candidates_*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                key = d.get("date", "")
                if key:
                    by_date[key] = d
            except Exception:
                continue

    if CURRENT_PATH.exists():
        try:
            d = json.loads(CURRENT_PATH.read_text(encoding="utf-8"))
            key = d.get("date", "")
            if key and key not in by_date:
                by_date[key] = d
        except Exception:
            pass

    return sorted(by_date.values(), key=lambda x: x.get("date", ""), reverse=True)


def compute_metrics(c: dict) -> dict:
    """notifier.fmt_candidate 와 동일한 계산 — 카드/모달 공용."""
    close = c.get("close", 0)
    target = c.get("target_median", 0)
    rec_pct = c.get("recommended_pct", 0)
    classification = classify_by_target(rec_pct)
    spec = DANTE_CHART_STATS[classification]
    sl_price = snap_to_tick(close * (1 + spec["sl_pct"] / 100))
    sl_pct = ((sl_price - close) / close * 100) if close else 0
    risk = max(close - sl_price, 0)
    rr = round((target - close) / risk, 2) if risk > 0 else None
    risk_level = assess_risk(sl_pct, rr)
    return {
        "close": close, "target": target, "rec_pct": rec_pct,
        "classification": classification, "target_range": spec["target_range"],
        "sl_price": sl_price, "sl_pct": sl_pct,
        "rr": rr, "risk_level": risk_level,
    }


def show_detail(c: dict) -> None:
    """모달 — 종목명을 다이얼로그 제목으로 (동적 title 위해 내부에서 @st.dialog)."""
    m = compute_metrics(c)
    risk_emoji = {"낮음": "🟢", "중간": "🟡", "높음": "🔴"}.get(m["risk_level"], "🟡")
    rr_disp = f"{m['rr']:.2f}" if m["rr"] is not None else "—"
    name = c.get("name", "")
    ticker = c.get("ticker", "")

    @st.dialog(name)
    def _dlg() -> None:
        st.caption(f"({ticker})")
        st.divider()

        # 1행: 가격 (좌) | 메타 (우)
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("💰 **현재가**")
            st.markdown(f"{m['close']:,}원")
            st.markdown("")
            st.markdown("🎯 **목표가**")
            st.markdown(f"{m['target']:,}원 ({m['rec_pct']:+.2f}%)")
            st.markdown("")
            st.markdown("🛑 **손절가**")
            st.markdown(f"{m['sl_price']:,}원 ({m['sl_pct']:+.2f}%)")
        with col2:
            st.markdown("📊 **분류**")
            st.markdown(f"{m['classification']}")
            st.markdown("")
            st.markdown("📈 **RR**")
            st.markdown(f"{rr_disp}")
            st.markdown("")
            st.markdown("**위험도**")
            st.markdown(f"{risk_emoji} {m['risk_level']}")

        st.divider()

        # 2행: 분할매수 (좌) | 선택 이유 (우)
        col3, col4 = st.columns(2)
        with col3:
            st.markdown("**📊 분할매수**")
            for stage_key, stage_val in (c.get("buy_stages") or {}).items():
                p = stage_val.get("price", 0)
                amt = stage_val.get("amount_krw", 0)
                st.markdown(f"{stage_key}: {p:,}원")
                st.caption(f"({amt:,}원)")
        with col4:
            st.markdown("**💡 선택 이유**")
            st.markdown("- 점수 4점")
            for key, val in (c.get("conditions") or {}).items():
                if not val:
                    continue
                lbl = cond_label(key)
                if lbl:
                    st.markdown(f"- {lbl}")

        if c.get("blacklisted"):
            st.warning(f"⚠️ 블랙리스트 — {c.get('blacklist_reason') or ''}")
        if c.get("recently_sold"):
            st.warning(f"⚠️ 최근 매도 이력: {c['recently_sold']}")

    _dlg()


def render_card(c: dict, idx: int, snap_date: str) -> None:
    """카드 표면 — 종목명/현재가/목표가/손절가 + [상세 보기] 버튼."""
    m = compute_metrics(c)
    name = c.get("name", "")
    ticker = c.get("ticker", "")

    warn = ""
    if c.get("blacklisted"):
        warn += " ⚠️BL"
    if c.get("recently_sold"):
        warn += " ⚠️매도"

    with st.container(border=True):
        st.markdown(f"**[{idx}] {name}** ({ticker}){warn}")
        st.markdown(f"💰 {m['close']:,}원")
        st.markdown(f"🎯 {m['target']:,}원 ({m['rec_pct']:+.0f}%)")
        st.markdown(f"🛑 {m['sl_price']:,}원")
        # key 에 날짜+ticker 결합 — 다른 페이지(날짜)로 이동해도 충돌 없음
        if st.button(
            "상세 보기",
            key=f"detail_{snap_date}_{ticker}",
            use_container_width=True,
        ):
            show_detail(c)


# ============================================================
# 메인
# ============================================================
st.set_page_config(page_title="국장 추천 종목", page_icon="📈", layout="wide")

# 다크/라이트 모드 토글 상태
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False

# 우측 상단 메뉴/헤더/푸터/툴바 숨김 — Streamlit 1.4x+ 신규 selector
hide_streamlit_style = """
<style>
/* 우측 상단 햄버거 메뉴 */
[data-testid="stMainMenu"] {display: none !important;}
[data-testid="stHeader"] {display: none !important;}
[data-testid="stToolbar"] {display: none !important;}
[data-testid="stDecoration"] {display: none !important;}

/* 푸터 */
footer {display: none !important;}
.stDeployButton {display: none !important;}

/* 키보드 단축키 트리거 버튼 비활성화 */
button[kind="header"] {display: none !important;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# 'C' / 'R' 등 Streamlit 단축키 다중 차단 + Clear caches 다이얼로그 자동 닫기
st.markdown("""
<script>
(function() {
    const blockedKeys = ['c', 'C', 'r', 'R'];
    const blockKeys = function(e) {
        if (blockedKeys.includes(e.key)) {
            const tag = (e.target.tagName || '').toLowerCase();
            if (tag !== 'input' && tag !== 'textarea') {
                e.stopImmediatePropagation();
                e.preventDefault();
                return false;
            }
        }
    };
    window.addEventListener('keydown', blockKeys, true);
    window.addEventListener('keypress', blockKeys, true);
    window.addEventListener('keyup', blockKeys, true);
    document.addEventListener('keydown', blockKeys, true);

    // Clear caches 모달이 떠도 즉시 닫음 (100ms 폴링)
    setInterval(function() {
        document.querySelectorAll('div[role="dialog"]').forEach(function(d) {
            if (d.textContent.includes('Clear caches')) {
                const closeBtn = d.querySelector('button[aria-label="Close"]');
                if (closeBtn) closeBtn.click();
                d.style.display = 'none';
            }
        });
    }, 100);
})();
</script>
""", unsafe_allow_html=True)

# 모달 다이얼로그 — 실제 DOM 구조(role="dialog")에 맞춰 정중앙 + 600px
st.markdown("""
<style>
/* 모달 다이얼로그 컨테이너 - 가운데 정렬 */
div[role="dialog"] {
    position: fixed !important;
    top: 50% !important;
    left: 50% !important;
    transform: translate(-50%, -50%) !important;
    max-width: 600px !important;
    width: 90% !important;
    max-height: 85vh !important;
    overflow-y: auto !important;
    margin: 0 !important;
}

/* 모달 안 마진 줄이기 */
div[role="dialog"] [data-testid="stMarkdownContainer"] p {
    margin: 0.15rem 0 !important;
    line-height: 1.4 !important;
}

div[role="dialog"] hr {
    margin: 0.5rem 0 !important;
}

div[role="dialog"] [data-testid="column"] {
    padding: 0 0.5rem !important;
}

/* Clear caches 다이얼로그 강제 숨김 (CSS 4 :has 지원 브라우저에서 동작) */
div[role="dialog"]:has(h2:contains("Clear caches")),
div[role="dialog"]:has(*:contains("Clear caches")) {
    display: none !important;
    visibility: hidden !important;
}

/* 종목 카드 그리드 — Flexbox 자동 줄바꿈 + 카드 최소 280px */
[data-testid="stHorizontalBlock"] {
    flex-wrap: wrap !important;
    gap: 1rem !important;
}
[data-testid="stHorizontalBlock"] > [data-testid="column"] {
    flex: 1 1 280px !important;
    min-width: 280px !important;
    max-width: 100% !important;
    width: auto !important;
}
[data-testid="stHorizontalBlock"] > [data-testid="column"] > div {
    width: 100% !important;
}
</style>
""", unsafe_allow_html=True)

# 다크모드 CSS — session_state 에 따라 조건부 주입
if st.session_state.dark_mode:
    st.markdown("""
    <style>
    /* 전체 배경 */
    .stApp {
        background-color: #0e1117 !important;
        color: #fafafa !important;
    }
    [data-testid="stAppViewContainer"] {
        background-color: #0e1117 !important;
    }
    /* 사이드바 */
    [data-testid="stSidebar"] {
        background-color: #1a1d23 !important;
    }
    /* 카드 / 컨테이너 */
    [data-testid="stContainer"] {
        background-color: #1a1d23 !important;
        border-color: #3a3f48 !important;
    }
    /* 텍스트 */
    .stApp, .stApp p, .stApp h1, .stApp h2, .stApp h3,
    .stApp h4, .stApp h5, .stApp h6, .stApp span, .stApp div {
        color: #fafafa !important;
    }
    [data-testid="stMarkdownContainer"] {
        color: #fafafa !important;
    }
    /* 버튼 */
    .stButton > button {
        background-color: #262730 !important;
        color: #fafafa !important;
        border-color: #3a3f48 !important;
    }
    .stButton > button:hover {
        background-color: #3a3f48 !important;
        border-color: #5a5f68 !important;
    }
    /* 셀렉트박스 */
    [data-testid="stSelectbox"] > div > div {
        background-color: #262730 !important;
        color: #fafafa !important;
    }
    /* 모달 다이얼로그 */
    div[role="dialog"] {
        background-color: #1a1d23 !important;
        color: #fafafa !important;
    }
    /* 디바이더 */
    hr {
        border-color: #3a3f48 !important;
    }
    /* 인포 / 알림 박스 */
    [data-testid="stAlert"] {
        background-color: #1a3a25 !important;
        color: #a7e8b8 !important;
    }
    </style>
    """, unsafe_allow_html=True)

# 타이틀 + 우측 상단 다크/라이트 토글
col_title, _col_spacer, col_theme = st.columns([6, 3, 1])
with col_title:
    st.title("추천 종목")
with col_theme:
    _icon = "🌙" if not st.session_state.dark_mode else "☀️"
    _label = "다크" if not st.session_state.dark_mode else "라이트"
    if st.button(f"{_icon} {_label}", key="theme_toggle", use_container_width=True):
        st.session_state.dark_mode = not st.session_state.dark_mode
        st.rerun()

snapshots = load_all_candidates()
n_pages = len(snapshots)

if n_pages == 0:
    st.error("❌ 매수 추천 데이터가 없습니다. candidates_v4.json / history/ 확인 필요.")
    st.stop()

if "page_idx" not in st.session_state:
    st.session_state.page_idx = 0

# 사이드바 — 날짜 선택 드롭다운만
date_options = [fmt_date_disp(s.get("date", "")) for s in snapshots]
selected = st.sidebar.selectbox(
    "날짜 선택",
    options=list(range(n_pages)),
    index=st.session_state.page_idx,
    format_func=lambda i: date_options[i],
)
if selected != st.session_state.page_idx:
    st.session_state.page_idx = selected
    st.rerun()

# 본문 상단 — 전체 통계 캡션
oldest = snapshots[-1].get("date", "")[:8]
newest = snapshots[0].get("date", "")[:8]
oldest_disp = f"{oldest[:4]}-{oldest[4:6]}-{oldest[6:]}" if len(oldest) >= 8 else oldest
newest_disp = f"{newest[:4]}-{newest[4:6]}-{newest[6:]}" if len(newest) >= 8 else newest
st.caption(f"📚 전체 {n_pages}일치  ·  범위 {oldest_disp} ~ {newest_disp}")

# 좌우 네비게이션 — 페이지 1 = 최신, 페이지 N = 가장 옛날
cur_idx = st.session_state.page_idx
is_oldest = cur_idx >= n_pages - 1
is_newest = cur_idx <= 0

col_prev, col_mid, col_next = st.columns([1, 2, 1])

if col_prev.button("◀ 이전 (과거)", disabled=is_oldest, use_container_width=True):
    st.session_state.page_idx = min(cur_idx + 1, n_pages - 1)
    st.rerun()

col_mid.markdown(
    f"<div style='text-align:center; padding-top:6px;'>"
    f"📅 <b>{cur_idx + 1} / {n_pages}</b> 페이지</div>",
    unsafe_allow_html=True,
)

if col_next.button("다음 (최신) ▶", disabled=is_newest, use_container_width=True):
    st.session_state.page_idx = max(cur_idx - 1, 0)
    st.rerun()

st.markdown("---")

# 본문
snap = snapshots[cur_idx]
market = snap.get("market_state", {})
bear = market.get("bear_market", False)
tradable = snap.get("tradable_candidates", []) or []
blacklisted = snap.get("blacklisted_candidates", []) or []

st.subheader(f"📅 {fmt_date_disp(snap.get('date', ''))}")

if bear:
    st.error("🐻 약세장 — 신규 매수 중단 권고")
else:
    st.success("🌞 강세장 — 매수 가능")

st.markdown(f"**총 {len(tradable)}종목 추천**")

if not tradable:
    st.info("ℹ️ 이 날짜에 v4 조건을 만족하는 매수 후보가 없습니다.")
else:
    sorted_cands = sorted(tradable, key=lambda c: c.get("recommended_pct", 0), reverse=True)
    snap_date = snap.get("date", "")
    # 5열 고정 — Flexbox CSS가 화면 폭에 따라 자동 줄바꿈
    n_cols = 5
    cols = st.columns(n_cols)
    for idx, c in enumerate(sorted_cands):
        with cols[idx % n_cols]:
            render_card(c, idx + 1, snap_date)

if blacklisted:
    st.markdown("---")
    with st.expander(f"⚠️ 블랙리스트 ({len(blacklisted)}종목 — 참고용, 매수 비권장)"):
        for c in blacklisted:
            st.markdown(
                f"- **{c.get('ticker', '')} {c.get('name', '')}**  "
                f"(목표 {c.get('recommended_pct', 0):+.2f}%)"
            )
