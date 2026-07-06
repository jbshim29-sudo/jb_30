"""5단계: analysis.json + stocks.json → 단일 HTML (대시보드 + 채널별 상세 리포트 통합).

한 파일 안에 상단=종합 대시보드, 하단=채널별 상세 리포트(계정당 A4 1장).
출력: output/dashboard_<date>.html
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .common import (BUCKET_LABELS, ROOT, data_dir_for, load_channels,
                     load_settings, log, read_json, today_kst_str)

_BORDER = {"pre": 0, "during": 1, "post": 2}


def _fmt_won(v) -> str:
    if v is None:
        return "-"
    try:
        v = int(v)
    except (ValueError, TypeError):
        return "-"
    jo = v // 10**12
    eok = (v % 10**12) // 10**8
    if jo > 0:
        return f"{jo}조 {eok:,}억" if eok else f"{jo}조"
    if eok > 0:
        return f"{eok:,}억"
    return f"{v:,}"


def _fmt_eok(v) -> str:
    if v is None:
        return "-"
    try:
        v = float(v)
    except (ValueError, TypeError):
        return "-"
    if abs(v) >= 10000:
        return f"{v/10000:,.1f}조"
    return f"{v:,.0f}억"


def _fmt_pct(v) -> str:
    if v is None:
        return "-"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def _sign_class(v) -> str:
    if v is None:
        return "flat"
    if v > 0:
        return "up"
    if v < 0:
        return "down"
    return "flat"


def _num(v) -> str:
    if v is None:
        return "-"
    try:
        f = float(v)
    except (ValueError, TypeError):
        return "-"
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


def _paragraphs(text: str) -> list[str]:
    """줄글 → 문단 리스트. 빈 줄 우선, 없으면 줄바꿈 기준."""
    if not text:
        return []
    text = text.replace("\r\n", "\n").strip()
    parts = text.split("\n\n") if "\n\n" in text else text.split("\n")
    return [p.strip() for p in parts if p.strip()]


def _basic_video(v: dict) -> dict:
    """collect 출력(videos.json) → 템플릿이 기대하는 형태(요약 필드는 비움)."""
    return {
        "id": v.get("id"),
        "channel": v.get("channel"),
        "title": v.get("title"),
        "url": v.get("url"),
        "bucket": v.get("bucket", "during"),
        "duration": v.get("duration"),
        "upload_ts": v.get("upload_ts"),
        "from_subtitle": v.get("has_subtitle", False),
        "description": (v.get("description") or "").strip(),
        "상세요약": "",
        "핵심요약": [],
        "시장전망": None,
        "언급종목": [],
    }


def _group_channels(videos: list[dict]) -> list[dict]:
    """config 채널 순서로 그룹화. 영상 없는 채널 제외, 내부는 개장전→중→후 정렬."""
    order = [c["name"] for c in load_channels()]
    by_channel: dict[str, list] = {name: [] for name in order}
    for v in videos:
        by_channel.setdefault(v.get("channel", "기타"), []).append(v)
    channels = []
    for name, vids in by_channel.items():
        if not vids:
            continue
        vids.sort(key=lambda x: _BORDER.get(x.get("bucket"), 1))
        bkts = sorted({v.get("bucket", "during") for v in vids},
                      key=lambda b: _BORDER.get(b, 1))
        channels.append({"name": name, "videos": vids, "buckets": bkts, "count": len(vids)})
    return channels


def _pick_content_date(date_str: str, settings: dict) -> str:
    """리포트에 쓸 날짜: 오늘 분석이 있으면 오늘, 없으면 가장 최근 이전 날짜(어제 등)."""
    base = ROOT / settings["paths"]["data_dir"]

    def has_videos(dt: str) -> bool:
        p = base / dt / "analysis.json"
        if not p.exists():
            return False
        try:
            return len(read_json(p).get("videos", [])) > 0
        except Exception:  # noqa: BLE001
            return False

    if has_videos(date_str):
        return date_str
    if base.exists():
        for dt in sorted((p.name for p in base.iterdir() if p.is_dir()), reverse=True):
            if dt < date_str and has_videos(dt):
                return dt
    return date_str


def build(date_str: str | None = None) -> Path:
    settings = load_settings()
    date_str = date_str or today_kst_str()
    ddir = data_dir_for(date_str, settings)
    dbase = ROOT / settings["paths"]["data_dir"]

    # 리포트(분석)는 최신 분석 날짜에서, 시장데이터는 오늘 것(현재값)에서.
    content_date = _pick_content_date(date_str, settings)
    is_prior = content_date != date_str
    cdir = dbase / content_date

    apath = cdir / "analysis.json"
    analysis = read_json(apath) if apath.exists() else {"videos": [], "overall": {}}

    # 시장(지수/수급/시총): 오늘 우선(현재 시세), 없으면 리포트 날짜 것
    if (ddir / "stocks.json").exists():
        stocks = read_json(ddir / "stocks.json")
    elif (cdir / "stocks.json").exists():
        stocks = read_json(cdir / "stocks.json")
    else:
        stocks = {"indices": {}, "top5": {}, "mentioned": [], "base_date": date_str,
                  "is_trading_day": False}

    # 언급종목 테이블은 리포트와 같은 날짜 것으로(일치)
    c_stocks = read_json(cdir / "stocks.json") if (cdir / "stocks.json").exists() else stocks
    mentioned = c_stocks.get("mentioned", [])

    videos = analysis.get("videos", [])
    no_analysis = False
    if not videos:
        # 분석이 전혀 없으면 수집 결과(제목·설명)로 폴백
        vpath = cdir / "videos.json"
        if vpath.exists():
            videos = [_basic_video(v) for v in read_json(vpath).get("videos", [])]
            no_analysis = True

    buckets = {"pre": [], "during": [], "post": []}
    for v in videos:
        buckets.get(v.get("bucket", "during"), buckets["during"]).append(v)
    channels = _group_channels(videos)

    env = Environment(
        loader=FileSystemLoader(str(ROOT / settings["paths"]["templates_dir"])),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["won"] = _fmt_won
    env.filters["eok"] = _fmt_eok
    env.filters["pct"] = _fmt_pct
    env.filters["signcls"] = _sign_class
    env.filters["num"] = _num
    env.filters["paras"] = _paragraphs
    env.filters["blabel"] = lambda b: BUCKET_LABELS.get(b, b)

    tmpl = env.get_template("page.html.j2")
    html = tmpl.render(
        date=date_str,
        stocks=stocks,
        overall=analysis.get("overall", {}),
        buckets=buckets,
        bucket_order=["pre", "during", "post"],
        bucket_labels=BUCKET_LABELS,
        mentioned=mentioned,
        channels=channels,
        video_count=len(videos),
        no_analysis=no_analysis,
        report_date=content_date,
        is_prior=is_prior,
    )

    # 단일 URL 산출: public/index.html 하나만 생성(날짜별 파일은 만들지 않음)
    pub = ROOT / "public"
    pub.mkdir(exist_ok=True)
    out = pub / "index.html"
    out.write_text(html, encoding="utf-8")
    log.info("페이지 생성(단일 URL) → %s", out)

    if settings.get("dashboard", {}).get("open_after_build"):
        try:
            webbrowser.open(out.as_uri())
        except Exception:  # noqa: BLE001
            pass
    return out


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    build(d)
