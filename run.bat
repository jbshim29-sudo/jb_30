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
set "RC=%errorlevel%"
echo [%date% %time%] 파이프라인 종료 (exit=%RC%)

REM ── 결과 페이지 자동 커밋/푸시 (Vercel 재배포 트리거) ──
REM   끄려면:  set AUTO_PUSH=0  후 실행
if "%AUTO_PUSH%"=="0" goto :done
if not "%RC%"=="0" goto :done
where git >nul 2>nul
if errorlevel 1 goto :done

echo [%date% %time%] 결과 커밋/푸시 시도 (로컬 딥분석 반영)
git add public/index.html data/*/analysis.json
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "local: 딥분석 갱신 %date%"
    git pull --rebase origin main
    git push
    echo [%date% %time%] 푸시 완료
) else (
    echo [%date% %time%] 변경 없음 - 푸시 생략
)

:done
endlocal
