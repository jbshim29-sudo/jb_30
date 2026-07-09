"""공통 유틸: 설정 로드, 경로, 로깅, KST 시간, JSON IO."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

# 프로젝트 루트 = 이 파일의 상위(src)의 상위
ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

KST = timezone(timedelta(hours=9))


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                "%H:%M:%S")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


log = get_logger("pipeline")


def load_settings() -> dict[str, Any]:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_channels() -> list[dict[str, Any]]:
    with open(CONFIG_DIR / "channels.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [c for c in data.get("channels", []) if c.get("enabled", True)]


def cookie_file(settings: dict) -> str | None:
    """yt-dlp 용 쿠키 파일 경로. 환경변수 YT_COOKIES_FILE 우선, 없으면 설정값.

    존재하는 파일일 때만 경로 반환(없으면 None → 쿠키 없이 진행).
    """
    p = os.getenv("YT_COOKIES_FILE") or (settings.get("youtube", {}) or {}).get("cookies_file")
    if p and Path(p).exists():
        return p
    return None


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst_str() -> str:
    return now_kst().strftime("%Y-%m-%d")


def target_date_kst(early_hour: int = 6) -> str:
    """파이프라인이 대상으로 삼을 날짜.

    새벽(기본 06시 이전) 실행은 '전날'을 대상으로 한다.
    GitHub 예약이 몇 시간 지연돼 KST 자정을 넘겨 실행되더라도, 빈 새 날이 아니라
    원래 의도한 그 날(장 마감된 전날)의 영상을 수집·분석하기 위함.
    """
    now = now_kst()
    if now.hour < early_hour:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def data_dir_for(date_str: str, settings: dict) -> Path:
    d = ROOT / settings["paths"]["data_dir"] / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_dir(settings: dict) -> Path:
    d = ROOT / settings["paths"]["output_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def classify_bucket(upload_ts_iso: str, settings: dict) -> str:
    """업로드 시각(KST ISO) → pre / during / post 버킷.

    upload_ts_iso 가 없거나 파싱 실패 시 'during' 기본값(중립).
    """
    if not upload_ts_iso:
        return "during"
    try:
        dt = datetime.fromisoformat(upload_ts_iso)
    except ValueError:
        return "during"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    dt = dt.astimezone(KST)
    market = settings["market"]
    open_h, open_m = (int(x) for x in market["open"].split(":"))
    close_h, close_m = (int(x) for x in market["close"].split(":"))
    t = dt.hour * 60 + dt.minute
    open_t = open_h * 60 + open_m
    close_t = close_h * 60 + close_m
    if t < open_t:
        return "pre"
    if t > close_t:
        return "post"
    return "during"


BUCKET_LABELS = {"pre": "개장전", "during": "개장중", "post": "개장후"}
