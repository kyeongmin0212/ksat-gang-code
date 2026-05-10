# KsatGang 대시보드

단테 v4 트레이딩 시스템 로컬 모니터링 대시보드 (Streamlit).

## 설치

```powershell
pip install streamlit pandas plotly holidays
```

## 실행

```powershell
cd C:\Users\sji48\ksat_gang\dashboard
streamlit run app.py
```

자동으로 `http://localhost:8501` 열림.

## 페이지 구성

- **🏠 메인 (`app.py`)** — 오늘 추천 종목 카드 그리드 (3열) + 위험도/분류/점수 필터
- **📅 히스토리 (`pages/1_📅_history.py`)** — 날짜별 추천 + 사후 1/5/10/20일 수익률 + KOSPI 알파
- **📊 성과 추적 (`pages/2_📊_performance.py`)** — 전체 history 통계 + 월별/일별 차트 + TOP10/BOTTOM10
- **🔍 종목 상세 (`pages/3_🔍_detail.py`)** — 캔들차트 + 일목구름 + MA60/MA224 + 진입/손절/목표 라인 + 추천 이력 + 매매일지

## 데이터 흐름

- **오늘 추천**: `candidates_v4.json` (분석 파이프라인이 매일 19:00 갱신)
- **히스토리**: `history/candidates_YYYY-MM-DD.json` (분석 시 자동 백업)
- **가격 데이터**: `stock_data.db` (SQLite, OHLCV + 인디케이터)

## 위험도 산정 (RR 기반)

- 🟢 낮음: RR ≥ 5 (단테 정통 1:5)
- 🟡 중간: RR ≥ 3 (단테 최소 1:3)
- 🔴 높음: RR < 3

## 손절가 산정 (단테 41장 평균)

- 스윙 (목표 < 30%): -6.24%
- 스윙&중장기 (30~50%): -8.92%
- 중장기 (50%+): -12.97%

## 트러블슈팅

- **빈 페이지** — `history/` 폴더 비었을 가능성. 분석 파이프라인 1회 이상 실행 필요.
- **사후 수익률 모두 `-`** — 추천일 + N영업일 후 종가 데이터가 DB에 아직 없음. 시간 경과 후 재확인.
- **차트 안 보임** — 종목 데이터가 DB에 없거나 OHLCV 필드 누락.
