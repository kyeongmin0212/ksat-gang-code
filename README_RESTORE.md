# 시스템 복구 가이드

새 PC에서 ksat_gang 시스템을 복구할 때 따라하는 단계.

⚠️ 이 저장소는 코드만 백업합니다. 다음은 별도 처리 필요:
- 추천 데이터: https://github.com/kyeongmin0212/ksat-gang-history
- DB: KRX에서 재수집 (수 시간 소요)
- 환경변수: 본인이 관리하는 비밀

## 1. 저장소 클론
```powershell
cd C:\Users\사용자명
git clone https://github.com/kyeongmin0212/ksat-gang-code.git ksat_gang
cd ksat_gang
```

## 2. Python 가상환경 + 패키지
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 3. 환경변수 설정
필요한 환경변수:
- TG_BOT_TOKEN: 텔레그램 봇 토큰
- TG_CHAT_ID: 텔레그램 채팅 ID
- KRX_ID: KRX 계정 (collector 사용 시)
- KRX_PW: KRX 비밀번호

PowerShell에서 (관리자):
```powershell
[Environment]::SetEnvironmentVariable("TG_BOT_TOKEN", "your_value", "User")
[Environment]::SetEnvironmentVariable("TG_CHAT_ID", "your_value", "User")
[Environment]::SetEnvironmentVariable("KRX_ID", "your_value", "User")
[Environment]::SetEnvironmentVariable("KRX_PW", "your_value", "User")
```
설정 후 PowerShell 재시작.

## 4. DB 재수집 (시간 오래 걸림)
빈 DB로 시작:
```powershell
python collector.py --mode 5y
```
(5년치 데이터 수집, 약 2~6시간)

또는 단축으로 1년만:
```powershell
python collector.py --mode 1y
```

## 5. 작업 스케줄러 등록
```powershell
.\scripts\setup_scheduler.ps1
```

## 6. GitHub 자격증명 등록 (history 자동 백업용)
새 PAT 발급 후:
```powershell
"protocol=https`nhost=github.com`nusername=kyeongmin0212`npassword=새토큰`n`n" | git credential approve
```

## 7. history 데이터 복원 (선택)
```powershell
cd C:\Users\사용자명\ksat_gang
git clone https://github.com/kyeongmin0212/ksat-gang-history.git history-backup
# history-backup 의 candidates_*.json 들을 history/ 폴더로 복사
```

## 8. 첫 분석 실행 (검증)
```powershell
.\runner_analysis_silent.vbs
```

## 9. 사이트 실행 (선택)
```powershell
streamlit run simple_app.py --server.port 8502
```

## 트러블슈팅

### Python 패키지 설치 실패
- Visual C++ Build Tools 필요할 수 있음
- pip install --upgrade pip setuptools 먼저 시도

### KRX 데이터 수집 실패
- KRX 계정 문제 확인
- pykrx 라이브러리 호환성 확인

### 텔레그램 알림 안 옴
- TG_BOT_TOKEN, TG_CHAT_ID 환경변수 확인
- @BotFather에서 봇 상태 확인
