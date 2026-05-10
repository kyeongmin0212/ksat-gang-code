"""성과 추적 페이지 — 전체 history 기반 통계 + 차트."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import utils as U


st.set_page_config(page_title="📊 성과 추적 — KsatGang", page_icon="📊", layout="wide")
st.markdown("<style>.stApp{background-color:#0e1117;color:#fafafa}h1,h2,h3{color:#d4af37}</style>", unsafe_allow_html=True)

st.title("📊 시스템 성과 추적")

dates = U.list_history_dates()
if not dates:
    st.warning("history/ 폴더에 저장된 추천 데이터가 없습니다.")
    st.info("매일 분석 파이프라인이 실행되면 자동 누적되며, 그때부터 통계가 의미 있습니다.")
    st.stop()

st.caption(f"🗓 분석 기간: {dates[-1]} ~ {dates[0]}  ({len(dates)}일치 데이터)")

n_days_pick = st.selectbox("기준 보유 기간 (영업일)", [1, 5, 10, 20], index=1)

with st.spinner("히스토리 집계 중..."):
    df = U.aggregate_history_performance(n_days_pick)

if df.empty:
    st.warning("아직 사후 가격 데이터가 충분하지 않습니다 (추천일 + N영업일 후 종가 필요).")
    st.stop()

ret_col = f"+{n_days_pick}일수익률(%)"
df = df.dropna(subset=[ret_col])

# ============================================================
# 핵심 통계
# ============================================================
total = len(df)
wins = int((df[ret_col] > 0).sum())
losses = int((df[ret_col] < 0).sum())
win_rate = wins / total * 100 if total else 0
avg_ret = df[ret_col].mean()
avg_win = df.loc[df[ret_col] > 0, ret_col].mean() if wins else 0
avg_loss = df.loc[df[ret_col] < 0, ret_col].mean() if losses else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("총 추천 (집계)", f"{total}건")
c2.metric(f"평균 +{n_days_pick}일", f"{avg_ret:+.2f}%")
c3.metric("승률", f"{win_rate:.1f}%", help=f"{wins} / {total}")
c4.metric("평균승", f"{avg_win:+.2f}%")
c5.metric("평균패", f"{avg_loss:+.2f}%")


# ============================================================
# 차트 1: 일별 평균 수익률
# ============================================================
st.subheader("📈 일별 평균 수익률 추이")
daily = df.groupby("추천일")[ret_col].agg(["mean", "count"]).reset_index()
daily.columns = ["추천일", "평균수익률(%)", "추천수"]
fig1 = px.bar(
    daily, x="추천일", y="평균수익률(%)",
    color="평균수익률(%)", color_continuous_scale=["#e74c3c", "#888", "#2ecc71"],
    color_continuous_midpoint=0,
    hover_data=["추천수"],
)
fig1.update_layout(template="plotly_dark", height=380)
st.plotly_chart(fig1, use_container_width=True)


# ============================================================
# 차트 2: Winners vs Losers 분포 (히스토그램)
# ============================================================
st.subheader("📊 Winners vs Losers 분포")
fig2 = go.Figure()
fig2.add_trace(go.Histogram(
    x=df.loc[df[ret_col] > 0, ret_col], name="Winners",
    marker_color="#2ecc71", opacity=0.8, nbinsx=30,
))
fig2.add_trace(go.Histogram(
    x=df.loc[df[ret_col] < 0, ret_col], name="Losers",
    marker_color="#e74c3c", opacity=0.8, nbinsx=30,
))
fig2.update_layout(
    template="plotly_dark", height=360, barmode="overlay",
    xaxis_title=f"+{n_days_pick}일 수익률(%)", yaxis_title="종목 수",
)
st.plotly_chart(fig2, use_container_width=True)


# ============================================================
# 차트 3: 월별 평균 수익률
# ============================================================
st.subheader("🗓 월별 평균 수익률")
df["월"] = df["추천일"].str[:7]
monthly = df.groupby("월")[ret_col].agg(["mean", "count"]).reset_index()
monthly.columns = ["월", "평균수익률(%)", "추천수"]
fig3 = px.bar(
    monthly, x="월", y="평균수익률(%)",
    color="평균수익률(%)", color_continuous_scale=["#e74c3c", "#888", "#2ecc71"],
    color_continuous_midpoint=0,
    hover_data=["추천수"],
)
fig3.update_layout(template="plotly_dark", height=360)
st.plotly_chart(fig3, use_container_width=True)


# ============================================================
# TOP 10 / BOTTOM 10
# ============================================================
st.subheader(f"🏆 +{n_days_pick}일 수익률 TOP 10 / BOTTOM 10")
c1, c2 = st.columns(2)
with c1:
    st.markdown("**🟢 TOP 10**")
    top = df.nlargest(10, ret_col)
    st.dataframe(top, use_container_width=True, hide_index=True)
with c2:
    st.markdown("**🔴 BOTTOM 10**")
    bot = df.nsmallest(10, ret_col)
    st.dataframe(bot, use_container_width=True, hide_index=True)
