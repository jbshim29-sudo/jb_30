"""오케스트레이터: 1) 수집 → 2) 전사 → 3) 분석 → 4) 종목 → 5) 대시보드.

사용:
  python -m src.pipeline                 # 오늘(KST)
  python -m src.pipeline --date 2026-07-04
  python -m src.pipeline --skip-whisper  # Whisper 생략
  python -m src.pipeline --only stocks   # 특정 단계만 (collect/transcribe/analyze/stocks/dashboard)
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from . import analyze as analyze_mod
from . import build_dashboard, collect_youtube, stocks, transcribe
from .common import log, target_date_kst

load_dotenv()


def run(date_str: str | None, skip_whisper: bool, only: str | None) -> None:
    # 새벽 실행(예약 지연으로 자정 넘김)은 전날을 대상으로 → 빈 날 분석 방지
    date_str = date_str or target_date_kst()
    log.info("=== 파이프라인 시작: %s ===", date_str)

    steps = ["collect", "transcribe", "analyze", "stocks", "dashboard"]
    if only:
        steps = [only]

    if "collect" in steps:
        log.info("[1/5] 유튜브 수집")
        collect_youtube.collect(date_str)
    if "transcribe" in steps and not skip_whisper:
        log.info("[2/5] Whisper 전사 (무자막 보완)")
        transcribe.transcribe(date_str)
    elif "transcribe" in steps:
        log.info("[2/5] Whisper 생략(--skip-whisper)")
    if "analyze" in steps:
        if os.getenv("ANTHROPIC_API_KEY"):
            log.info("[3/5] Claude 분석")
            analyze_mod.analyze(date_str)
        else:
            log.info("[3/5] ANTHROPIC_API_KEY 없음 → AI 분석 생략 "
                     "(시장데이터 + 영상목록 모드). 키 등록 시 자동 요약 활성화.")
    if "stocks" in steps:
        log.info("[4/5] 종목/지수 데이터")
        stocks.build_stocks(date_str)
    if "dashboard" in steps:
        log.info("[5/5] 통합 페이지 생성 (대시보드 + 채널별 리포트)")
        out = build_dashboard.build(date_str)
        log.info("완료 → %s", out)

    log.info("=== 파이프라인 종료 ===")


def main() -> None:
    p = argparse.ArgumentParser(description="경제 유튜브 → 대시보드 파이프라인")
    p.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    p.add_argument("--skip-whisper", action="store_true")
    p.add_argument("--only", choices=["collect", "transcribe", "analyze",
                                      "stocks", "dashboard"])
    args = p.parse_args()
    run(args.date, args.skip_whisper, args.only)


if __name__ == "__main__":
    main()
