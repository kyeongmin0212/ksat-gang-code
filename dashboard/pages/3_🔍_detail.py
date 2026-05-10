"""종목 상세 페이지 — 차트 + 단테 점수 + 추천 이력 + 매매일지."""
from __future__ import annotations

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import utils as U


st.set_page_config(page_title="🔍 종목 상세 — KsatGang", page_icon="🔍", layout="wide")
st.markdown("<style>.stApp{background-color:#0e1117;color:#fafafa}h1,h2,h3{color:#d4af37}</style>", unsafe_allow_html=True)

st.title("🔍 종목 상세")


# ============================================================
# 종목 선택 — query param 우선, 없으면 selectbox
# ============================================================
qp = st.query_params
default_ticker = qp.get("ticker") or ""
default_ref    = qp.get("ref_date") or ""

# 모든 history + 오늘 후보에서 사용 가능한 종목 모음
all_tickers: dict[str, str] = {}  # ticker → name
today = U.load_candidates()
for c in today.get("tradable_candidates") or []:
    all_tickers[c["ticker"]] = c.get("name", "")
for d in U.list_history_dates():
    h = U.load_candidates(d)
    for c in h.get("tradable_candidates") or []:
        all_tickers.setdefault(c["ticker"], c.get("name", ""))

if not all_tickers:
    st.warning("저장된 추천 데이터가 없습니다.")
    st.stop()

ticker_options = sorted(all_tickers.keys(), key=lambda t: all_tickers[t])
default_idx = ticker_options.index(default_ticker) if default_ticker in ticker_options else 0
labels = [f"{t} — {all_tickers[t]}" for t in ticker_options]

c1, c2 = st.columns([3, 1])
with c1:
    sel_label = st.selectbox("📋 종목 선택", labels, index=default_idx)
    ticker = ticker_options[labels.index(sel_label)]
    name = all_tickers[ticker]
with c2:
    ref_date_input = st.text_input("기준일 (YYYY-MM-DD, 비우면 오늘)", value=default_ref or "")


# ============================================================
# 추천 이력 — 이 종목이 언제 추천됐는지
# ============================================================
history_records: list[dict] = []
for d in U.list_history_dates():
    h = U.load_candidates(d)
    for c in h.get("tradable_candidates") or []:
        if c.get("ticker") == ticker:
            history_records.append({
                "추천일":   d,
                "현재가":   c.get("close", 0),
                "분류":     "스윙" if c.get("recommended_pct", 0) < 30 else
                            ("스윙&중장기" if c.get("recommended_pct", 0) < 50 else "중장기"),
                "점수":     c.get("score", 0),
                "추천수익률": c.get("recommended_pct", 0),
                "조건":     c.get("conditions", {}),
            })
history_records.sort(key=lambda r: r["추천일"], reverse=True)


# ============================================================
# 헤더 — 현재 데이터 (오늘 또는 ref_date)
# ============================================================
ref_date_for_chart = ref_date_input if ref_date_input else (today.get("date", "")[:4] + "-" + today.get("date", "")[4:6] + "-" + today.get("date", "")[6:] if today.get("date") else None)

# 차트용 시계열
ohlcv = U.get_ohlcv_series(ticker, days_back=180, ref_date=ref_date_for_chart)
inds  = U.get_indicator_series(ticker, days_back=180, ref_date=ref_date_for_chart)

if ohlcv.empty:
    st.error(f"OHLCV 데이터 없음: {ticker}")
    st.stop()

current_close = float(ohlcv["종가"].iloc[-1])
st.markdown(f"### {name}  `{ticker}`  · 종가 **{int(current_close):,}원**")


# ============================================================
# 추천 정보 — 가장 최근 history 또는 오늘
# ============================================================
sel_rec = None
for c in today.get("tradable_candidates") or []:
    if c.get("ticker") == ticker:
        sel_rec = c
        break
if sel_rec is None and history_records:
    most_recent = history_records[0]
    h = U.load_candidates(most_recent["추천일"])
    for c in h.get("tradable_candidates") or []:
        if c.get("ticker") == ticker:
            sel_rec = c
            break

# 진입가/손절가/목표가 라인 그리기
entry_line = sel_rec.get("close") if sel_rec else None
target_line = sel_rec.get("target_median") if sel_rec else None
sl_line = None
if sel_rec:
    rp = sel_rec.get("recommended_pct", 0)
    sl_pct = -6.24 if rp < 30 else (-8.92 if rp < 50 else -12.97)
    sl_line = round(entry_line * (1 + sl_pct / 100)) if entry_line else None


# ============================================================
# 캔들스틱 차트 + 일목구름 + MA60/MA224
# ============================================================
st.subheader("📊 차트 (180거래일)")
fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
    row_heights=[0.75, 0.25],
    subplot_titles=("OHLCV + 일목구름 + MA60/MA224", "거래량"),
)

# 캔들
fig.add_trace(
    go.Candlestick(
        x=ohlcv["날짜"], open=ohlcv["시가"], high=ohlcv["고가"],
        low=ohlcv["저가"], close=ohlcv["종가"], name="OHLC",
        increasing_line_color="#e74c3c", decreasing_line_color="#3498db",
    ),
    row=1, col=1,
)

# 일목구름 (선행스팬1, 2 사이 음영)
if not inds.empty and "span_a_std" in inds.columns:
    merged = ohlcv[["날짜"]].merge(inds, on="날짜", how="left")
    fig.add_trace(
        go.Scatter(x=merged["날짜"], y=merged["span_a_std"],
                   line=dict(color="rgba(120,180,255,0.3)", width=1),
                   name="선행스팬1"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=merged["날짜"], y=merged["span_b_std"],
                   line=dict(color="rgba(120,180,255,0.5)", width=1),
                   fill="tonexty", fillcolor="rgba(120,180,255,0.15)",
                   name="구름대"),
        row=1, col=1,
    )
    # 기준선
    fig.add_trace(
        go.Scatter(x=merged["날짜"], y=merged["base_std"],
                   line=dict(color="#d4af37", width=1.2),
                   name="기준선(26)"),
        row=1, col=1,
    )
    # MA60 / MA224
    if "ma60" in merged.columns:
        fig.add_trace(
            go.Scatter(x=merged["날짜"], y=merged["ma60"],
                       line=dict(color="#2ecc71", width=1), name="MA60"),
            row=1, col=1,
        )
    if "ma224" in merged.columns:
        fig.add_trace(
            go.Scatter(x=merged["날짜"], y=merged["ma224"],
                       line=dict(color="#aaa", width=1.2, dash="dash"),
                       name="MA224 (부모 라인)"),
            row=1, col=1,
        )

# 진입/손절/목표 가로선
if entry_line:
    fig.add_hline(y=entry_line, line_color="#d4af37", line_dash="dot",
                  annotation_text=f"진입 {int(entry_line):,}", annotation_position="right",
                  row=1, col=1)
if sl_line:
    fig.add_hline(y=sl_line, line_color="#e74c3c", line_dash="dash",
                  annotation_text=f"손절 {int(sl_line):,}", annotation_position="right",
                  row=1, col=1)
if target_line:
    fig.add_hline(y=target_line, line_color="#2ecc71", line_dash="dash",
                  annotation_text=f"목표 {int(target_line):,}", annotation_position="right",
                  row=1, col=1)

# 거래량
fig.add_trace(
    go.Bar(x=ohlcv["날짜"], y=ohlcv["거래량"], name="거래량",
           marker_color="rgba(180,180,180,0.4)"),
    row=2, col=1,
)

fig.update_layout(
    template="plotly_dark", height=620,
    xaxis_rangeslider_visible=False,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
)
st.plotly_chart(fig, use_container_width=True)


# ============================================================
# 단테 점수 상세 분석
# ============================================================
st.subheader("🏷 단테 진입 조건 (가장 최근 추천)")
if sel_rec:
    conds = sel_rec.get("conditions", {}) or {}
    cond_label = {
        "cloud_above_std": "일목 구름대 위 (강한 추세)",
        "cloud_above_2x":  "단테 2배 구름대 위",
        "base_line_near":  "일목 기준선 ±2% 근접 ★",
        "ma_convergence":  "MA5/20/60 수렴",
        "volume_surge":    "거래량 급증 (200%+)",
        "accumulation_bar":"매집봉 (300%+)",
        "bb_lower_touch":  "볼린저 하단 터치",
        "base_line_not_overheated": "기준선 이격 7% 미만 ★",
    }
    rows = []
    for k, lbl in cond_label.items():
        rows.append({"조건": lbl, "충족": "✅" if conds.get(k) else "—"})
    st.table(pd.DataFrame(rows))
    st.caption(f"단테 점수: {sel_rec.get('score', 0)}점 / 8점  ·  ★ = v4 필수 조건")
else:
    st.info("이 종목은 history 에 추천 기록이 없습니다.")


# ============================================================
# 추천 이력
# ============================================================
st.subheader("📜 추천 이력")
if history_records:
    hist_df = pd.DataFrame([
        {
            "추천일":     r["추천일"],
            "추천일종가": f"{r['현재가']:,}원",
            "분류":       r["분류"],
            "점수":       r["점수"],
            "추천수익률": f"{r['추천수익률']:+.1f}%",
        }
        for r in history_records
    ])
    st.dataframe(hist_df, use_container_width=True, hide_index=True)
else:
    st.info("이 종목은 추천 이력이 없습니다.")


# ============================================================
# 매매 일지
# ============================================================
st.subheader("📝 매매 일지 (로컬 저장)")
JOURNAL = U.BASE / "history" / f"journal_{ticker}.json"
journal_text = ""
if JOURNAL.exists():
    journal_text = JOURNAL.read_text(encoding="utf-8")

new_text = st.text_area("이 종목에 대한 메모/매매 결정/회고",
                         value=journal_text, height=160,
                         placeholder="예) 1차 매수 진입 / 손절 -10% / 목표 +30% ...")
if st.button("💾 저장"):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL.write_text(new_text, encoding="utf-8")
    st.success(f"저장됨: {JOURNAL.name}")
