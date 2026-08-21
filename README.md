# ksat-gang — 국내주식 백테스팅 · 추천 시스템

투자 기법을 코드로 구현하고 **백테스팅으로 검증한 뒤, 매일 자동으로 매수 후보를 추려
텔레그램으로 알려주는** 개인 프로젝트입니다.

`2026.03 ~ 2026.05` · Python

> 이 저장소는 **로컬에서 개발한 코드의 백업 미러**입니다.
> 커밋은 백업 자동화 봇(`ksat-gang-bot`)이 수행하므로 개발 히스토리를 담고 있지 않습니다.
> 데이터(약 1.1GB SQLite)와 추천 이력은 용량·분리 원칙에 따라 제외했으며,
> 추천 이력은 별도 저장소 [`ksat-gang-history`](https://github.com/kyeongmin0212/ksat-gang-history)에 있습니다.

<br>

## 만든 이유

투자 기법을 설명하는 콘텐츠는 많지만 **"그래서 실제로 통하느냐"**를 확인할 방법이 없었습니다.
기법을 규칙으로 옮기고, 과거 데이터로 돌려보고, 손실이 난 경우를 따로 분석해
**전략을 정량적으로 개선하는 과정**을 직접 만들어 보고 싶었습니다.

<br>

## 백테스트 결과 (v4 기준)

| 지표 | 값 |
|---|---|
| 검증 기간 | 2021-04-23 ~ 2026-04-22 (**5년**) |
| CAGR (연평균 수익률) | **52.95 %** |
| 누적 수익률 | **735.81 %** |
| 샤프 지수 | **1.84** |
| MDD (최대 낙폭) | **-17.45 %** |
| 승률 | **76.83 %** |

> ⚠️ **이 수치는 낙관적입니다.** 과거 데이터에 맞춰 조건을 조정한 결과라
> 과최적화가 섞여 있습니다. 슬리피지·거래 지연·유동성 제약을 감안한
> **실전 기대치는 CAGR 25~35 % 수준**으로 보수적으로 잡고 운용했습니다.
>
> 수익률만 보지 않고 **샤프 지수와 MDD를 함께 산출**해, 위험 대비 성과로 판단했습니다.

<br>

## 시스템 구조

```
[전략 도출]
유튜브 자막(VTT)     dante_analysis/vtt_parser.py    자막 → 중복 제거 텍스트
       ↓                        extractor.py         발언에서 매매 규칙 추출
       ↓                        aggregate.py         규칙 통합 · 정리
       ↓
[운용 파이프라인]
KRX 일봉 수집        collector.py       KOSPI · KOSDAQ 전 종목 → SQLite (약 1.1GB)
       ↓
종목 필터            filter.py          시가총액 · 거래량 조건으로 후보 축소
       ↓
전략 판정            dante_strategy.py  이동평균 · 볼린저 · 거래량 조합
       ↓
백테스트             backtesting.py     5년 구간 시뮬레이션 → CAGR · 샤프 · MDD
       ↓
성과 분석            analyze_*.py       승패 분류 · 손실 원인 · 월별 손익
       ↓
알림 / 대시보드      notifier.py        텔레그램 실시간 통보
                     dashboard/         Streamlit 4페이지 시각화
```

**매일 19:00 Windows 작업 스케줄러로 자동 실행**됩니다
(`runner_analysis.ps1`, `runner_notify.ps1`, `setup_scheduler.ps1`).

<br>

## 전략을 코드로 옮긴 방법

기법이 영상으로만 설명되어 있어, **유튜브 자막을 파싱해 규칙을 텍스트로 추출**하는
파이프라인을 먼저 만들었습니다.

| 파일 | 역할 |
|---|---|
| `vtt_parser.py` | VTT 자막에서 타임스탬프·태그 제거, 중복 문장 정리 |
| `extractor.py` | 정리된 텍스트에서 매매 조건 추출 |
| `market_extractor.py` | 시장 국면 관련 언급 분리 |
| `aggregate.py` · `batch_processor.py` | 여러 영상의 규칙을 통합 · 일괄 처리 |

이렇게 뽑은 규칙을 `dante_strategy.py`의 판정 로직으로 구현했습니다.

<br>

## 주요 기능

### 데이터 수집 (`collector.py`)
- KRX에서 **KOSPI · KOSDAQ 전 종목** 일봉 수집
- 시가 · 고가 · 저가 · 종가 · 거래량 · 거래대금 · 시가총액
- SQLite 저장, `(종목코드, 날짜)` / `(시장구분, 날짜)` 복합 인덱스로 조회 최적화
- 누적 약 **1.1GB**

### 전략 구현 (`dante_strategy.py`)
- 이동평균 **ma5 / ma20 / ma60 / ma112 / ma200 / ma224 / ma448**
- 볼린저 밴드, 거래량, 시가총액 조건 조합
- 장기 이평선(224 · 448)까지 사용해 추세 국면을 판정

### 백테스팅 (`backtesting.py`, 55KB)
- 과거 구간을 순회하며 매수·매도 시뮬레이션
- 보유 기간 · 월별 손익 · 승률 산출

### 성과 분석
| 파일 | 내용 |
|---|---|
| `analyze_loss_reasons.py` | **손실 거래의 원인을 유형별로 분류** |
| `analyze_v4_holding_period.py` | 보유 기간과 수익률의 관계 |
| `analyze_dante_classification.py` | 신호 분류 정확도 |
| `monthly_pnl_*.py` | 월별 손익 집계 |

### 전략 개선 이력
```
v2 → v4 → v5 → v6
```
버전을 바꿀 때마다 `compare_v4_v5.py`, `compare_v4_v6.py`로 **이전 버전과 성과를
직접 비교**해, 감이 아니라 수치로 개선 여부를 판단했습니다.

### 실시간 알림 (`notifier.py`)
- 매수 신호 발생 시 **종목 · 매수가** 통보
- 보유 종목의 **현재 수익률** 주기적 통보
- 텔레그램 봇 연동

### 모니터링 대시보드 (`dashboard/`, Streamlit 4페이지)
| 페이지 | 내용 |
|---|---|
| 🏠 메인 | 오늘 추천 종목 카드 그리드 + 위험도 · 분류 · 점수 필터 |
| 📅 히스토리 | 날짜별 추천 + **사후 1/5/10/20일 수익률 + KOSPI 대비 알파** |
| 📊 성과 추적 | 전체 통계 + 월별 · 일별 차트 + TOP10 / BOTTOM10 |
| 🔍 종목 상세 | 캔들차트 · 일목구름 · MA60 / MA224 + 진입 · 손절 · 목표 라인 |

추천이 실제로 맞았는지 **사후 검증**하고, 시장(KOSPI) 대비 초과수익까지 추적합니다.

<br>

## 기술 스택

`Python` `SQLite` `pandas` `Streamlit` `Plotly` `Telegram Bot API` `KRX` `PowerShell`

<br>

## 보안 · 운영 설계

- API 키 · 계정 정보는 전부 **환경변수**로 분리 (코드에 하드코딩 없음)
  - `TG_BOT_TOKEN` `TG_CHAT_ID` `KRX_ID` `KRX_PW`
- `.gitignore`로 제외한 것
  - `*.db` — 1.1GB, GitHub 100MB 제한
  - `logs/` — 실행 로그
  - `history/` — 추천 이력 (별도 저장소로 분리)
- 새 PC 복구 절차는 [`README_RESTORE.md`](README_RESTORE.md)에 정리

<br>

## 실행

```bash
git clone https://github.com/kyeongmin0212/ksat-gang-code.git ksat_gang
cd ksat_gang

python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

환경변수 설정 후 데이터 수집부터 실행합니다. 자세한 절차는 `README_RESTORE.md` 참고.

<br>

## 한계

- 특정 투자 기법 하나를 구현한 것으로, 다른 시장 국면에서의 일반화는 검증되지 않았습니다
- 백테스트는 수수료·슬리피지를 단순화해 반영했습니다
- 국내 시장(KOSPI · KOSDAQ) 전용입니다
