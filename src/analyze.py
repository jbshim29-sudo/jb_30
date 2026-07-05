"""3단계: Claude 로 영상별 구조화 요약 + 종합 분석.

입력: data/<date>/videos.json (+ subs/*.txt)
출력: data/<date>/analysis.json
  {
    date,
    videos: [ {id, channel, title, url, bucket, duration, 핵심요약[], 시장전망{방향,근거},
               언급종목:[{name,코멘트,방향성}], 키워드[] } ],
    overall: { 핵심이슈3줄[], 컨센서스[], 엇갈리는시각[], 반복언급종목:[{name,count,채널[]}] }
  }
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .common import (data_dir_for, load_settings, log, read_json,
                     today_kst_str, write_json)

load_dotenv()

# ── 개별 영상 요약 스키마 (tool 강제) ───────────────────────────────
VIDEO_TOOL = {
    "name": "record_video_summary",
    "description": "경제 유튜브 영상 1개의 분석 결과를 구조화해 기록",
    "input_schema": {
        "type": "object",
        "properties": {
            "핵심요약": {
                "type": "array", "items": {"type": "string"},
                "description": "영상의 핵심 내용 3~5개 불릿 (한국어)",
            },
            "시장전망": {
                "type": "object",
                "properties": {
                    "방향": {"type": "string", "enum": ["강세", "약세", "중립", "혼조", "불명"]},
                    "근거": {"type": "string"},
                },
                "required": ["방향", "근거"],
            },
            "언급종목": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "정식 종목명(가능한 한 상장사명)"},
                        "코멘트": {"type": "string"},
                        "방향성": {"type": "string", "enum": ["긍정", "중립", "부정"]},
                    },
                    "required": ["name", "방향성"],
                },
            },
            "키워드": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["핵심요약", "시장전망", "언급종목", "키워드"],
    },
}

OVERALL_TOOL = {
    "name": "record_overall",
    "description": "여러 채널 영상 요약을 종합한 당일 시장 인사이트",
    "input_schema": {
        "type": "object",
        "properties": {
            "핵심이슈3줄": {"type": "array", "items": {"type": "string"},
                          "description": "오늘의 핵심 이슈 3줄 요약"},
            "컨센서스": {"type": "array", "items": {"type": "string"},
                       "description": "여러 채널이 공통으로 말하는 시각"},
            "엇갈리는시각": {"type": "array", "items": {"type": "string"}},
            "반복언급종목": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "count": {"type": "integer"},
                        "채널": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "count"],
                },
            },
        },
        "required": ["핵심이슈3줄", "컨센서스", "엇갈리는시각", "반복언급종목"],
    },
}


def _client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 없습니다 (.env 확인)")
    import anthropic
    return anthropic.Anthropic(api_key=key)


def _call_tool(client, model, max_tokens, tool, tool_name, system, user_text):
    """tool_choice 강제 → 구조화 JSON(dict) 반환."""
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user_text}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    raise RuntimeError(f"{tool_name} tool_use 응답 없음")


def _load_transcript(video: dict, settings: dict) -> tuple[str, bool]:
    """(본문텍스트, 자막기반여부). 자막 없으면 제목+설명 폴백."""
    path = video.get("subtitle_path")
    if path and Path(path).exists():
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        max_c = settings["claude"]["max_transcript_chars"]
        if len(text) > max_c:
            # map-reduce 대신 앞/중/뒤 샘플로 압축(간단·저비용). 필요시 청크확장 가능.
            chunk = settings["claude"]["chunk_chars"]
            head = text[:chunk]
            mid_start = max(0, len(text) // 2 - chunk // 2)
            mid = text[mid_start:mid_start + chunk]
            tail = text[-chunk:]
            text = f"{head}\n...\n{mid}\n...\n{tail}"
        return text, True
    fallback = f"[자막 없음 - 제목/설명 기반]\n제목: {video.get('title')}\n설명: {video.get('description','')}"
    return fallback, False


def _summarize_video(client, settings, video: dict) -> dict:
    model = settings["claude"]["summary_model"]
    max_tokens = settings["claude"]["max_tokens"]
    body, from_sub = _load_transcript(video, settings)
    system = (
        "당신은 한국 주식시장 전문 애널리스트입니다. 아래 경제 유튜브 영상의 "
        "내용을 분석해 record_video_summary 도구로 구조화해 기록하세요. "
        "종목명은 가능한 한 정식 상장사명으로 정규화하고, 근거 없는 추측은 피하세요."
    )
    user = f"채널: {video['channel']}\n제목: {video['title']}\n\n[본문]\n{body}"
    try:
        data = _call_tool(client, model, max_tokens, VIDEO_TOOL,
                          "record_video_summary", system, user)
    except Exception as e:  # noqa: BLE001
        log.warning("영상 요약 실패 %s: %s", video["id"], e)
        data = {"핵심요약": ["(분석 실패)"], "시장전망": {"방향": "불명", "근거": ""},
                "언급종목": [], "키워드": []}
    return {
        "id": video["id"],
        "channel": video["channel"],
        "title": video["title"],
        "url": video["url"],
        "bucket": video["bucket"],
        "duration": video.get("duration"),
        "from_subtitle": from_sub,
        **data,
    }


def _overall(client, settings, summaries: list[dict]) -> dict:
    if not summaries:
        return {"핵심이슈3줄": [], "컨센서스": [], "엇갈리는시각": [], "반복언급종목": []}
    model = settings["claude"]["model"]
    max_tokens = settings["claude"]["max_tokens"]
    # 요약들만 압축 전달
    compact = []
    for s in summaries:
        compact.append({
            "채널": s["channel"], "제목": s["title"],
            "핵심요약": s["핵심요약"], "시장전망": s["시장전망"],
            "언급종목": [{"name": m["name"], "방향성": m.get("방향성")} for m in s["언급종목"]],
        })
    system = (
        "당신은 한국 주식시장 전략가입니다. 여러 경제 유튜브 채널의 당일 요약을 종합해 "
        "record_overall 도구로 시장 인사이트를 기록하세요. 반복 언급 종목은 실제 등장 채널 수로 집계하세요."
    )
    user = "당일 채널별 요약(JSON):\n" + json.dumps(compact, ensure_ascii=False)
    try:
        return _call_tool(client, model, max_tokens, OVERALL_TOOL,
                          "record_overall", system, user)
    except Exception as e:  # noqa: BLE001
        log.warning("종합 분석 실패: %s", e)
        return {"핵심이슈3줄": [], "컨센서스": [], "엇갈리는시각": [], "반복언급종목": []}


def analyze(date_str: str | None = None) -> dict:
    settings = load_settings()
    date_str = date_str or today_kst_str()
    ddir = data_dir_for(date_str, settings)
    videos_path = ddir / "videos.json"
    if not videos_path.exists():
        log.warning("videos.json 없음 → 수집 먼저 필요")
        return {"videos": [], "overall": {}}

    videos = read_json(videos_path).get("videos", [])
    if not videos:
        log.info("분석할 영상 없음")
        result = {"date": date_str, "videos": [], "overall": {}}
        write_json(ddir / "analysis.json", result)
        return result

    client = _client()
    summaries = []
    for v in videos:
        log.info("분석 %s [%s] %s", v["channel"], v["bucket"], (v["title"] or "")[:40])
        summaries.append(_summarize_video(client, settings, v))

    overall = _overall(client, settings, summaries)
    result = {"date": date_str, "videos": summaries, "overall": overall}
    write_json(ddir / "analysis.json", result)
    log.info("분석 완료 → %s", ddir / "analysis.json")
    return result


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    analyze(d)
