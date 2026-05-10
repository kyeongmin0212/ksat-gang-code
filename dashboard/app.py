"""KsatGang 대시보드 — 메인 페이지 (오늘의 추천)."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import pandas as pd

import utils as U


# ============================================================
# 페이지 설정
# ============================================================
st.set_page_config(
    page_title="KsatGang — 단테 v4 대시보드",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 다크/골드 스타일
st.markdown("""
<style>
:root {
  --gold: #d4af37;
  --gold-soft: #b8941f;
}
.stApp { background-color: #0e1117; color: #fafafa; }
h1, h2, h3 { color: var(--gold); }
.metric-card {
  background: linear-gradient(135deg, #1a1d24, #14171c);
  border: 1px solid #2a2d33;
  border-left: 4px solid var(--gold);
  padding: 14px 16px;
  border-radius: 10px;
  box-shadow: 0 2px 6px rgba(0,0,0,0.35);
  margin-bottom: 8px;
}
.cand-card {
  background: linear-gradient(135deg, #1c1f26, #15181d);
  border: 1px solid #2a2d33;
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 12px;
  box-shadow: 0 2px 6px rgba(0,0,0,0.35);
}
.cand-card.green { border-left: 4px solid #2ecc71; }
.cand-card.yellow { border-left: 4px solid var(--gold); }
.cand-card.red { border-left: 4px solid #e74c3c; }
.cand-name { font-size: 1.1em; font-weight: bold; color: var(--gold); }
.cand-code { font-size: 0.85em; color: #888; }
.kv-row { display: flex; justify-content: space-between; margin: 3px 0; font-size: 0.9em; }
.kv-key { color: #aaa; }
.kv-val { color: #fafafa; font-weight: 500; }
.bull { color: #2ecc71; font-weight: bold; }
.bear { color: #e74c3c; font-weight: bold; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# 데이터 로드
# ============================================================
data = U.load_candidates()  # candidates_v4.json (오늘)
df = U.candidates_to_df(data)

ref_date = data.get("date", "")
ref_disp = f"{ref_date[:4]}-{ref_date[4:6]}-{ref_date[6:]}" if len(ref_date) == 8 else ref_date
market = data.get("market_state", {})
is_bull = market.get("is_bull", True)
bear = market.get("bear_market", False)
summary = data.get("filter_summary", {})
backtest = data.get("backtest_reference", {})


# ============================================================
# 헤더
# ============================================================
st.title("📈 KsatGang — 단테 v4 매수 추천")

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(
        f'<div class="metric-card"><div class="kv-key">분석 기준일</div>'
        f'<div class="kv-val" style="font-size:1.4em">{ref_disp}</div></div>',
        unsafe_allow_html=True,
    )
with c2:
    market_label = '<span class="bull">🌞 강세장</span>' if is_bull else '<span class="bear">⚠️ 약세장</span>'
    st.markdown(
        f'<div class="metric-card"><div class="kv-key">시장 상태</div>'
        f'<div class="kv-val" style="font-size:1.4em">{market_label}</div></div>',
        unsafe_allow_html=True,
    )
with c3:
    st.markdown(
        f'<div class="metric-card"><div class="kv-key">추천 종목</div>'
        f'<div class="kv-val" style="font-size:1.4em">{summary.get("tradable_candidates", 0)}건</div></div>',
        unsafe_allow_html=True,
    )
with c4:
    st.markdown(
        f'<div class="metric-card"><div class="kv-key">v4 백테스트 CAGR</div>'
        f'<div class="kv-val" style="font-size:1.4em">+{backtest.get("cagr_pct", 0):.1f}%</div></div>',
        unsafe_allow_html=True,
    )

st.caption(
    f"v4 백테스트 (5년): CAGR {backtest.get('cagr_pct', 0):.1f}% / "
    f"Sharpe {backtest.get('sharpe', 0):.2f} / MDD {backtest.get('mdd_pct', 0):.1f}% / "
    f"승률 {backtest.get('win_rate_pct', 0):.1f}% — 실전 예상 CAGR 25~35%"
)

if bear:
    st.error("🛑 약세장 (KOSPI 200MA 미만) — 신규 매수 중단 권장. 기존 보유 종목은 계획대로 매도.")


# ============================================================
# 사이드바 — 필터 + 정렬
# ============================================================
st.sidebar.header("🔧 필터 / 정렬")

if df.empty:
    st.warning("오늘 추천 종목이 없습니다.")
    st.stop()

risk_filter = st.sidebar.multiselect(
    "위험도", options=["낮음", "중간", "높음"],
    default=["낮음", "중간", "높음"],
)
class_filter = st.sidebar.multiselect(
    "분류", options=sorted(df["분류"].unique().tolist()),
    default=sorted(df["분류"].unique().tolist()),
)
score_min = st.sidebar.slider("점수 ≥", 1, 8, 4)
sort_by = st.sidebar.selectbox(
    "정렬", ["RR", "추천수익률(%)", "점수", "현재가"],
    index=0,
)
sort_desc = st.sidebar.checkbox("내림차순", value=True)

filtered = df[
    df["위험도"].isin(risk_filter)
    & df["분류"].isin(class_filter)
    & (df["점수"] >= score_min)
].copy()
filtered = filtered.sort_values(sort_by, ascending=not sort_desc, na_position="last")

st.sidebar.divider()
st.sidebar.metric("필터 통과", f"{len(filtered)}건")
st.sidebar.markdown("---")
st.sidebar.markdown("**위험도 기준 (RR)**")
st.sidebar.markdown("🟢 RR ≥ 5  /  🟡 RR ≥ 3  /  🔴 RR < 3")


# ============================================================
# 카드 그리드 (3열)
# ============================================================
st.subheader(f"📌 추천 종목 ({len(filtered)}건)")

if filtered.empty:
    st.info("필터 조건을 만족하는 종목이 없습니다.")
else:
    cols = st.columns(3)
    for i, (_, row) in enumerate(filtered.iterrows()):
        col = cols[i % 3]
        with col:
            color_class = {"낮음": "green", "중간": "yellow", "높음": "red"}[row["위험도"]]
            risk_emoji = U.risk_emoji(row["위험도"])
            buy_range = f"{int(row['분할매수_low']):,}~{int(row['분할매수_high']):,}원"
            blacklisted_badge = " 🚫블랙" if row["블랙리스트"] else ""

            html = f"""
            <div class="cand-card {color_class}">
              <div class="cand-name">{row['종목명']} {blacklisted_badge}</div>
              <div class="cand-code">{row['종목코드']} · {row['시장']} · {row['분류']}</div>
              <hr style="border:0;border-top:1px solid #2a2d33;margin:8px 0">
              <div class="kv-row"><span class="kv-key">현재가</span>
                <span class="kv-val">{int(row['현재가']):,}원</span></div>
              <div class="kv-row"><span class="kv-key">분할매수</span>
                <span class="kv-val">{buy_range}</span></div>
              <div class="kv-row"><span class="kv-key">손절가</span>
                <span class="kv-val">{int(row['손절가']):,}원 ({row['손절률(%)']:+.2f}%)</span></div>
              <div class="kv-row"><span class="kv-key">목표가</span>
                <span class="kv-val">{int(row['목표가']):,}원 ({row['추천수익률(%)']:+.1f}%)</span></div>
              <div class="kv-row"><span class="kv-key">RR / 점수</span>
                <span class="kv-val">{row['RR']:.2f} · {row['점수']}점</span></div>
              <div class="kv-row"><span class="kv-key">위험도</span>
                <span class="kv-val">{risk_emoji} {row['위험도']}</span></div>
            </div>
            """
            st.markdown(html, unsafe_allow_html=True)
            # 상세보기 버튼 — query param 으로 detail 페이지 호출
            if st.button(f"🔍 상세", key=f"detail_{row['종목코드']}", use_container_width=True):
                st.query_params["ticker"] = row["종목코드"]
                st.query_params["ref_date"] = ref_date
                st.switch_page("pages/3_🔍_detail.py")

st.divider()
st.subheader("📊 표 보기")
st.dataframe(
    filtered.assign(
        RR=lambda d: d["RR"].apply(lambda v: f"{v:.2f}" if v else "-"),
    )[
        ["종목코드", "종목명", "분류", "점수", "위험도",
         "현재가", "분할매수_low", "분할매수_high", "손절가",
         "목표가", "추천수익률(%)", "RR"]
    ],
    use_container_width=True,
    hide_index=True,
)
