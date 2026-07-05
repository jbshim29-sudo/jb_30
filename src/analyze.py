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
# 주의: Anthropic tool input_schema 의 property 키는 ASCII(^[a-zA-Z0-9_.-])만 허용.
# 따라서 스키마는 영문 키를 쓰고, 응답 후 _remap_video/_remap_overall 에서 한글 필드로 변환.
VIDEO_TOOL = {
    "name": "record_video_summary",
    "description": "경제 유튜브 영상 1개의 분석 결과를 구조화해 기록",
    "input_schema": {
        "type": "object",
        "properties": {
            "detail_summary": {
                "type": "string",
                "description": (
                    "영상을 보지 않은 사람도 이 글만 읽으면 내용 전체를 파악할 수 있도록 쓴 "
                    "A4 약 1장 분량(1,200~1,800자)의 충실한 줄글 요약(한국어). "
                    "①배경·맥락(왜 이 주제인가) ②핵심 주장·논지 ③구체적 근거·수치·데이터 "
                    "④언급 종목별 분석과 논리 ⑤시장 전망 ⑥투자 시사점·리스크 순으로 4~6개 문단 서술. "
                    "각 문단은 빈 줄(\\n\\n)로 구분. 불릿 금지, 진행자의 논리 흐름을 그대로 살린 자연스러운 문장으로."
                ),
            },
            "key_points": {
                "type": "array", "items": {"type": "string"},
                "description": "영상의 핵심 내용 3~5개 불릿 (한국어)",
            },
            "market_outlook": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["강세", "약세", "중립", "혼조", "불명"]},
                    "basis": {"type": "string", "description": "전망 근거(한국어)"},
                },
                "required": ["direction", "basis"],
            },
            "mentioned_stocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "정식 종목명(가능한 한 상장사명)"},
                        "comment": {"type": "string", "description": "해당 종목 코멘트(한국어)"},
                        "stance": {"type": "string", "enum": ["긍정", "중립", "부정"]},
                    },
                    "required": ["name", "stance"],
                },
            },
            "keywords": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["detail_summary", "key_points", "market_outlook",
                     "mentioned_stocks", "keywords"],
    },
}

OVERALL_TOOL = {
    "name": "record_overall",
    "description": "여러 채널 영상 요약을 종합한 당일 시장 인사이트",
    "input_schema": {
        "type": "object",
        "properties": {
            "top_issues": {"type": "array", "items": {"type": "string"},
                           "description": "오늘의 핵심 이슈 3줄 요약(한국어)"},
            "consensus": {"type": "array", "items": {"type": "string"},
                          "description": "여러 채널이 공통으로 말하는 시각(한국어)"},
            "divergences": {"type": "array", "items": {"type": "string"},
                            "description": "엇갈리는 시각(한국어)"},
            "repeated_stocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "count": {"type": "integer"},
                        "channels": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "count"],
                },
            },
        },
        "required": ["top_issues", "consensus", "divergences", "repeated_stocks"],
    },
}


def _as_list(v) -> list:
    if isinstance(v, list):
        return v
    if v in (None, ""):
        return []
    return [v]


def _remap_video(d: dict) -> dict:
    """ASCII 키 응답 → 한글 필드. 모델 출력 형태 변형(문자열 등)에 방어적."""
    if not isinstance(d, dict):
        d = {}
    o = d.get("market_outlook")
    o = o if isinstance(o, dict) else {}
    stocks = []
    for m in _as_list(d.get("mentioned_stocks")):
        if isinstance(m, dict):
            stocks.append({"name": m.get("name"), "코멘트": m.get("comment", ""),
                           "방향성": m.get("stance")})
        elif isinstance(m, str) and m.strip():
            stocks.append({"name": m.strip(), "코멘트": "", "방향성": None})
    return {
        "상세요약": d.get("detail_summary", "") if isinstance(d.get("detail_summary"), str) else "",
        "핵심요약": [x for x in _as_list(d.get("key_points")) if isinstance(x, str)],
        "시장전망": {"방향": o.get("direction", "불명"), "근거": o.get("basis", "")},
        "언급종목": stocks,
        "키워드": [x for x in _as_list(d.get("keywords")) if isinstance(x, str)],
    }


def _remap_overall(d: dict) -> dict:
    if not isinstance(d, dict):
        d = {}
    reps = []
    for r in _as_list(d.get("repeated_stocks")):
        if isinstance(r, dict):
            reps.append({"name": r.get("name"), "count": r.get("count", 0),
                         "채널": _as_list(r.get("channels"))})
        elif isinstance(r, str) and r.strip():
            reps.append({"name": r.strip(), "count": 1, "채널": []})
    return {
        "핵심이슈3줄": [x for x in _as_list(d.get("top_issues")) if isinstance(x, str)],
        "컨센서스": [x for x in _as_list(d.get("consensus")) if isinstance(x, str)],
        "엇갈리는시각": [x for x in _as_list(d.get("divergences")) if isinstance(x, str)],
        "반복언급종목": reps,
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
        "특히 '상세요약'은 영상을 못 본 사람도 이 글만 읽으면 핵심을 한 번에 파악할 수 있도록 "
        "자연스러운 줄글로 충실히 작성하세요(불릿 금지, 문단 구분). "
        "종목명은 가능한 한 정식 상장사명으로 정규화하고, 근거 없는 추측은 피하세요."
    )
    user = f"채널: {video['channel']}\n제목: {video['title']}\n\n[본문]\n{body}"
    try:
        raw = _call_tool(client, model, max_tokens, VIDEO_TOOL,
                         "record_video_summary", system, user)
        data = _remap_video(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("영상 요약 실패 %s: %s", video["id"], e)
        data = {"상세요약": "(분석 실패)", "핵심요약": ["(분석 실패)"],
                "시장전망": {"방향": "불명", "근거": ""}, "언급종목": [], "키워드": []}
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
        return _remap_overall(_call_tool(client, model, max_tokens, OVERALL_TOOL,
                                         "record_overall", system, user))
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

    # 증분: 이미 분석된 영상 재사용, 새 영상만 Claude 호출 (2시간마다 실행 시 비용 절감)
    analysis_path = ddir / "analysis.json"
    prev = read_json(analysis_path) if analysis_path.exists() else {}
    done = {s["id"]: s for s in prev.get("videos", [])
            if s.get("상세요약") and s.get("상세요약") != "(분석 실패)"}

    client = None
    summaries = []
    new_count = 0
    for v in videos:
        cached = done.get(v["id"])
        if cached:
            summaries.append(cached)
            continue
        if client is None:
            client = _client()
        log.info("분석 %s [%s] %s", v["channel"], v["bucket"], (v["title"] or "")[:40])
        summaries.append(_summarize_video(client, settings, v))
        new_count += 1

    log.info("영상 %d개 (신규 분석 %d, 재사용 %d)", len(videos), new_count, len(videos) - new_count)

    # 새 영상이 없고 이전 종합이 있으면 재사용, 아니면 종합 재계산
    if new_count == 0 and prev.get("overall"):
        overall = prev["overall"]
    else:
        if client is None:
            client = _client()
        overall = _overall(client, settings, summaries)

    result = {"date": date_str, "videos": summaries, "overall": overall}
    write_json(analysis_path, result)
    log.info("분석 완료 → %s", analysis_path)
    return result


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    analyze(d)
