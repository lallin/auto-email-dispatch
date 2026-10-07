@echo off
rem 사무실 공유 모드로 메일 발송 서버를 실행합니다 (사무실 내부망에서만 접속 가능)
cd /d "%~dp0"
set HOST=0.0.0.0
set PYTHONIOENCODING=utf-8
python server.py
pause
