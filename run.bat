@echo off
REM 매일 경제 유튜브 분석 파이프라인 실행 (수동/스케줄러 공용)
setlocal
cd /d "%~dp0"

REM venv 있으면 사용, 없으면 시스템 python
if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [%date% %time%] 파이프라인 시작
"%PY%" -m src.pipeline %*
echo [%date% %time%] 파이프라인 종료 (exit=%errorlevel%)

endlocal
