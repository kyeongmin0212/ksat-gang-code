# 환경변수 설정 가이드 스크립트
# 사용 전: 아래 값을 본인 정보로 수정한 후 실행

Write-Host "================================================"
Write-Host "ksat_gang 환경변수 설정"
Write-Host "================================================"
Write-Host ""
Write-Host "이 스크립트를 실행하기 전 아래 값을 직접 수정하세요."
Write-Host "또는 PowerShell에서 직접 실행:"
Write-Host ""
Write-Host "  [Environment]::SetEnvironmentVariable('TG_BOT_TOKEN', 'your_value', 'User')"
Write-Host ""

# 아래 줄 주석 풀고 실제 값 입력
# [Environment]::SetEnvironmentVariable("TG_BOT_TOKEN", "여기에_토큰", "User")
# [Environment]::SetEnvironmentVariable("TG_CHAT_ID", "여기에_챗ID", "User")
# [Environment]::SetEnvironmentVariable("KRX_ID", "여기에_KRX_ID", "User")
# [Environment]::SetEnvironmentVariable("KRX_PW", "여기에_KRX_PW", "User")

Write-Host ""
Write-Host "현재 환경변수 상태 확인:"
Write-Host ""
@("TG_BOT_TOKEN", "TG_CHAT_ID", "KRX_ID", "KRX_PW") | ForEach-Object {
    $val = [Environment]::GetEnvironmentVariable($_, "User")
    if ($val) {
        Write-Host "  $_: ✅ 설정됨"
    } else {
        Write-Host "  $_: ❌ 미설정"
    }
}
