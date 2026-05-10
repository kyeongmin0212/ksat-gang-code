"""히스토리 페이지 — 날짜 선택 + 사후 수익률."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, date

import streamlit as st
import pandas as pd

import utils as U


st.set_page_config(page_title="📅 히스토리 — KsatGang", page_icon="📅", layout="wide")
st.markdown("<style>.stApp{background-color:#0e1117;color:#fafafa}h1,h2,h3{color:#d4af37}</style>", unsafe_allow_html=True)

st.title("📅 추천 히스토리 + 사후 수익률")

# ============================================================
# 날짜 선택
# ============================================================
dates = U.list_history_dates()
if not dates:
    st.warning("history/ 폴더에 저장된 추천 데이터가 없습니다.")
    st.info("매일 분석 파이프라인 실행 시 자동 누적됩니다 — `dante_strategy.py --mode daily`")
    st.stop()

c1, c2 = st.columns([1, 3])
with c1:
    selected = st.selectbox("📆 추천 날짜 선택", options=dates, index=0)
with c2:
    st.metric("저장된 날짜 수", f"{len(dates)}일",
              help=f"가장 오래된: {dates[-1]} / 가장 최근: {dates[0]}")

data = U.load_candidates(selected)
ref_date_yyyymmdd = data.get("date", "").replace("-", "")
df = U.candidates_to_df(data)
market = data.get("market_state", {})
is_bull = market.get("is_bull", True)

st.caption(
    f"기준일 {selected} · "
    f"{'🌞 강세장' if is_bull else '⚠️ 약세장'} · "
    f"추천 {len(df)}건 · "
    f"블랙리스트 {data.get('filter_summary', {}).get('blacklisted_count', 0)}건"
)

if df.empty:
    st.info(f"{selected} 추천 종목 없음")
    st.stop()


# ============================================================
# 사후 수익률 계산 (1/5/10/20일)
# ============================================================
N_DAYS = [1, 5, 10, 20]
st.subheader("📊 추천 후 수익률 (영업일 기준)")

with st.spinner("사후 수익률 계산 중..."):
    rows = []
    for _, row in df.iterrows():
        t = row["종목코드"]
        entry = row["현재가"]
        sl = row["손절가"]
        target = row["목표가"]
        perf = U.compute_returns_at(t, ref_date_yyyymmdd, entry, N_DAYS)
        sl_hit = False
        target_hit = False
        # 손절/목표 도달 여부 (1~20일 사이 종가 기준 단순 체크)
        for n in N_DAYS:
            c = perf[n]["close"]
            if c is None:
                continue
            if not sl_hit and c <= sl:
                sl_hit = True
            if not target_hit and c >= target:
                target_hit = True
        out = {
            "종목코드": t,
            "종목명":   row["종목명"],
            "분류":     row["분류"],
            "위험도":   row["위험도"],
            "추천일종가": int(entry),
        }
        for n in N_DAYS:
            r = perf[n]["ret_pct"]
            out[f"+{n}일 수익률"] = f"{r:+.2f}%" if r is not None else "-"
        # KOSPI 비교
        for n in N_DAYS:
            kr = U.get_kospi_return(ref_date_yyyymmdd, n)
            r = perf[n]["ret_pct"]
            if r is not None and kr is not None:
                alpha = round(r - kr, 2)
                out[f"+{n}일 알파"] = f"{alpha:+.2f}%"
            else:
                out[f"+{n}일 알파"] = "-"
        out["손절도달"] = "🔴" if sl_hit else "—"
        out["목표도달"] = "🟢" if target_hit else "—"
        rows.append(out)

perf_df = pd.DataFrame(rows)


# ============================================================
# 요약 메트릭
# ============================================================
def avg_pct(col: str) -> float | None:
    vals = []
    for v in perf_df[col]:
        if v == "-": continue
        try: vals.append(float(v.replace("%", "").replace("+", "")))
        except Exception: pass
    return sum(vals) / len(vals) if vals else None

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("종목 수", len(perf_df))
for col, m in zip(["+5일 수익률", "+10일 수익률", "+20일 수익률"], [m2, m3, m4]):
    a = avg_pct(col)
    m.metric(col, f"{a:+.2f}%" if a is not None else "-")
sl_count = int((perf_df["손절도달"] == "🔴").sum())
target_count = int((perf_df["목표도달"] == "🟢").sum())
m5.metric("손절/목표", f"🔴 {sl_count} · 🟢 {target_count}")


# ============================================================
# 표
# ============================================================
view = st.radio("표시 방식", ["테이블", "카드"], horizontal=True)

if view == "테이블":
    st.dataframe(perf_df, use_container_width=True, hide_index=True)
else:
    cols = st.columns(3)
    for i, (_, r) in enumerate(perf_df.iterrows()):
        col = cols[i % 3]
        sl_emoji = r["손절도달"]; tg_emoji = r["목표도달"]
        with col:
            with st.container(border=True):
                st.markdown(f"**{r['종목명']}** ({r['종목코드']}) · {r['분류']}")
                st.caption(f"추천일 종가: {r['추천일종가']:,}원 · {U.risk_emoji(r['위험도'])} {r['위험도']}")
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("+1일", r["+1일 수익률"])
                k2.metric("+5일", r["+5일 수익률"])
                k3.metric("+10일", r["+10일 수익률"])
                k4.metric("+20일", r["+20일 수익률"])
                st.caption(f"손절도달 {sl_emoji}  ·  목표도달 {tg_emoji}")
