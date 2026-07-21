@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   기업 위험 뉴스 모니터링 시스템 - 로컬 서버를 시작합니다.
echo   브라우저가 자동으로 열립니다. (열리지 않으면 http://127.0.0.1:8765 )
echo   * API 키는 화면에서 입력합니다. 키는 이 창(서버)의 메모리에만
echo     저장되고 파일에 남지 않습니다. 창을 닫으면 사라집니다.
echo   * 종료: 이 창에서 Ctrl+C.
echo ============================================================
where python >nul 2>nul
if %errorlevel%==0 (
  python -m src.api.server
) else (
  py -m src.api.server
)
pause
