"""채널별 상세 리포트 페이지 생성 (대시보드와 별도).

읽으면 한 번에 이해되는 채널별 1~2페이지 줄글 요약.
채널명 헤더 + 개장전/중/후 배지 + 서술형 상세요약.

출력: output/report_<date>.html
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .common import (BUCKET_LABELS, ROOT, data_dir_for, load_channels,
                     load_settings, log, output_dir, read_json, today_kst_str)


def _paragraphs(text: str) -> list[str]:
    """줄글 → 문단 리스트. 빈 줄 우선, 없으면 줄바꿈 기준."""
    if not text:
        return []
    text = text.replace("\r\n", "\n").strip()
    if "\n\n" in text:
        parts = text.split("\n\n")
    else:
        parts = text.split("\n")
    return [p.strip() for p in parts if p.strip()]


def build(date_str: str | None = None) -> Path:
    settings = load_settings()
    date_str = date_str or today_kst_str()
    ddir = data_dir_for(date_str, settings)

    analysis = read_json(ddir / "analysis.json") if (ddir / "analysis.json").exists() \
        else {"videos": [], "overall": {}}
    videos = analysis.get("videos", [])

    # 채널 순서 = config 순서 유지, 영상 없는 채널 제외
    order = [c["name"] for c in load_channels()]
    by_channel: dict[str, list] = {name: [] for name in order}
    for v in videos:
        by_channel.setdefault(v.get("channel", "기타"), []).append(v)
    # 각 채널 내부는 개장전→중→후 순 정렬
    border = {"pre": 0, "during": 1, "post": 2}
    channels = []
    for name in list(by_channel.keys()):
        vids = by_channel.get(name) or []
        if not vids:
            continue
        vids.sort(key=lambda x: border.get(x.get("bucket"), 1))
        buckets = sorted({v.get("bucket", "during") for v in vids},
                         key=lambda b: border.get(b, 1))
        channels.append({"name": name, "videos": vids, "buckets": buckets,
                         "count": len(vids)})

    env = Environment(
        loader=FileSystemLoader(str(ROOT / settings["paths"]["templates_dir"])),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["paras"] = _paragraphs
    env.filters["blabel"] = lambda b: BUCKET_LABELS.get(b, b)

    tmpl = env.get_template("report.html.j2")
    html = tmpl.render(
        date=date_str,
        channels=channels,
        total_videos=len(videos),
        bucket_labels=BUCKET_LABELS,
    )

    out = output_dir(settings) / f"report_{date_str}.html"
    out.write_text(html, encoding="utf-8")
    log.info("리포트 생성 → %s", out)

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
