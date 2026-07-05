"""5단계: analysis.json + stocks.json → 단일 HTML 대시보드.

출력: output/dashboard_<date>.html
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .common import (BUCKET_LABELS, ROOT, data_dir_for, load_settings, log,
                     output_dir, read_json, today_kst_str)


def _fmt_won(v) -> str:
    """원 단위 정수 → '조 억' 한국식."""
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
    """억원 단위 숫자(네이버 재무) → 표기."""
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
    """한국식: 상승 red, 하락 blue."""
    if v is None:
        return "flat"
    if v > 0:
        return "up"
    if v < 0:
        return "down"
    return "flat"


def _num(v) -> str:
    """정수는 천단위 콤마, 소수는 소수 둘째자리까지."""
    if v is None:
        return "-"
    try:
        f = float(v)
    except (ValueError, TypeError):
        return "-"
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


def build(date_str: str | None = None) -> Path:
    settings = load_settings()
    date_str = date_str or today_kst_str()
    ddir = data_dir_for(date_str, settings)

    analysis = read_json(ddir / "analysis.json") if (ddir / "analysis.json").exists() \
        else {"videos": [], "overall": {}}
    stocks = read_json(ddir / "stocks.json") if (ddir / "stocks.json").exists() \
        else {"indices": {}, "top5": {}, "mentioned": [], "base_date": date_str,
              "is_trading_day": False}

    # 버킷별 영상 그룹
    buckets = {"pre": [], "during": [], "post": []}
    for v in analysis.get("videos", []):
        buckets.get(v.get("bucket", "during"), buckets["during"]).append(v)

    env = Environment(
        loader=FileSystemLoader(str(ROOT / settings["paths"]["templates_dir"])),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["won"] = _fmt_won
    env.filters["eok"] = _fmt_eok
    env.filters["pct"] = _fmt_pct
    env.filters["signcls"] = _sign_class
    env.filters["num"] = _num
    env.filters["blabel"] = lambda b: BUCKET_LABELS.get(b, b)

    tmpl = env.get_template("dashboard.html.j2")
    html = tmpl.render(
        date=date_str,
        stocks=stocks,
        overall=analysis.get("overall", {}),
        buckets=buckets,
        bucket_order=["pre", "during", "post"],
        bucket_labels=BUCKET_LABELS,
        mentioned=stocks.get("mentioned", []),
        video_count=len(analysis.get("videos", [])),
    )

    out = output_dir(settings) / f"dashboard_{date_str}.html"
    out.write_text(html, encoding="utf-8")
    log.info("대시보드 생성 → %s", out)

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
