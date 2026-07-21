#!/usr/bin/env bash
# 기업 위험 뉴스 모니터링 시스템 - 로컬 서버 시작(맥/리눅스)
# 실행 권한이 필요하면: chmod +x start_news_risk_monitor.sh
set -e
cd "$(dirname "$0")"
echo "============================================================"
echo "  기업 위험 뉴스 모니터링 시스템 - 로컬 서버를 시작합니다."
echo "  브라우저가 자동으로 열립니다. (열리지 않으면 http://127.0.0.1:8765 )"
echo "  * API 키는 화면에서 입력합니다. 키는 서버 메모리에만 저장되고"
echo "    파일에 남지 않습니다. 서버를 끄면 사라집니다."
echo "  * 종료: 이 터미널에서 Ctrl+C."
echo "============================================================"
if command -v python3 >/dev/null 2>&1; then
  python3 -m src.api.server
else
  python -m src.api.server
fi
