"""1단계: yt-dlp로 채널별 당일 업로드 영상 목록 + 한국어 자막 수집.

산출물: data/<date>/videos.json
  [
    { id, title, url, channel, upload_ts(KST ISO), upload_date, duration,
      description, bucket(pre/during/post), has_subtitle, subtitle_path }
  ]
자막 vtt는 data/<date>/subs/<id>.ko.vtt 로 저장, 순수 텍스트는 subtitle_path(.txt).
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import requests
from yt_dlp import YoutubeDL

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_RSS_HEADERS = {"User-Agent": "Mozilla/5.0"}

from .common import (KST, classify_bucket, cookie_file, data_dir_for,
                     load_channels, load_settings, log, read_json,
                     today_kst_str, write_json)


def _apply_yt_opts(opts: dict, settings: dict) -> dict:
    """쿠키 + player_client 등 공통 yt-dlp 옵션 주입."""
    cf = cookie_file(settings)
    if cf:
        opts["cookiefile"] = cf
    clients = settings.get("youtube", {}).get("player_clients")
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": list(clients)}}
    return opts


def _channel_videos_url(channel_id: str) -> str:
    """채널ID(UC...) 또는 핸들(@...) → /videos 탭 URL."""
    cid = channel_id.strip()
    if cid.startswith("@"):
        return f"https://www.youtube.com/{cid}/videos"
    if cid.startswith("UC"):
        return f"https://www.youtube.com/channel/{cid}/videos"
    # 그 외: 그대로 사용
    return cid


def _rss_times(channel_id: str, timeout: int = 10) -> dict[str, datetime]:
    """채널 RSS 피드에서 {video_id: 업로드시각(KST datetime)}.

    RSS 는 봇 차단이 없어 클라우드(GitHub) IP 에서도 정확한 <published> 시각을 준다.
    (yt-dlp 는 클라우드에서 timestamp 가 누락돼 전부 자정→개장전으로 몰리는 문제 우회)
    """
    out: dict[str, datetime] = {}
    try:
        r = requests.get(RSS_URL.format(channel_id), headers=_RSS_HEADERS, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        log.warning("RSS 조회 실패 %s: %s", channel_id, e)
        return out
    for block in r.text.split("<entry>")[1:]:
        vm = re.search(r"<yt:videoId>([^<]+)", block)
        pm = re.search(r"<published>([^<]+)", block)
        if not (vm and pm):
            continue
        try:
            out[vm.group(1)] = datetime.fromisoformat(pm.group(1)).astimezone(KST)
        except ValueError:
            continue
    return out


def _parse_upload_ts(entry: dict, rss_times: dict | None = None) -> tuple[str | None, str | None]:
    """업로드 시각 → (KST ISO, YYYY-MM-DD).

    우선순위: ①RSS <published>(클라우드에서도 정확) ②yt-dlp timestamp ③upload_date(자정).
    """
    vid = entry.get("id")
    if rss_times and vid in rss_times:
        dt = rss_times[vid]
        return dt.isoformat(), dt.strftime("%Y-%m-%d")
    ts = entry.get("timestamp")
    if ts:
        dt = datetime.fromtimestamp(ts, tz=KST)
        return dt.isoformat(), dt.strftime("%Y-%m-%d")
    ud = entry.get("upload_date")  # 'YYYYMMDD'
    if ud and len(ud) == 8:
        dt = datetime.strptime(ud, "%Y%m%d").replace(tzinfo=KST)
        return dt.isoformat(), dt.strftime("%Y-%m-%d")
    return None, None


def _clean_vtt(vtt_text: str) -> str:
    """VTT 자막 → 순수 텍스트. 타임스탬프/헤더/중복 인접 라인 제거."""
    lines: list[str] = []
    prev = None
    for raw in vtt_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        # 타임코드 라인 (00:00:00.000 --> 00:00:02.000 ...)
        if "-->" in line:
            continue
        # 순번 라인 (숫자만)
        if line.isdigit():
            continue
        # 인라인 태그 제거 <c> <00:00:00.000> 등
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line:
            continue
        if line == prev:
            continue
        lines.append(line)
        prev = line
    return "\n".join(lines)


def _list_today_videos(channel: dict, settings: dict, today: str) -> list[dict]:
    """채널의 최근 영상 중 당일(today, KST) 업로드만 메타 추출."""
    url = _channel_videos_url(channel["channel_id"])
    max_scan = settings["youtube"]["max_scan_per_channel"]
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,        # 개별 메타(timestamp) 필요 → flat 아님
        "playlistend": max_scan,
        "ignoreerrors": True,
        "skip_download": True,
        # 다운로드가 아니라 메타데이터만 필요 → 포맷 확인 실패(클라우드에서 흔함)를
        # 무시하고 제목/업로드시각만 추출. 이거 없으면 'Requested format is not
        # available' 로 항목이 통째로 버려져 0개가 됨.
        "ignore_no_formats_error": True,
    }
    _apply_yt_opts(ydl_opts, settings)
    results: list[dict] = []
    log.info("[%s] 영상 목록 조회: %s", channel["name"], url)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] 목록 조회 실패: %s", channel["name"], e)
        return results

    # RSS 로 정확한 업로드 시각 확보(클라우드 timestamp 누락 우회)
    channel_id = (info or {}).get("channel_id")
    rss_times = _rss_times(channel_id) if channel_id else {}

    entries = (info or {}).get("entries") or []
    for entry in entries:
        if not entry:
            continue
        iso, ud = _parse_upload_ts(entry, rss_times)
        if ud != today:
            continue
        vid = entry.get("id")
        results.append({
            "id": vid,
            "title": entry.get("title"),
            "url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
            "channel": channel["name"],
            "upload_ts": iso,
            "upload_date": ud,
            "duration": entry.get("duration"),
            "description": (entry.get("description") or "")[:2000],
            "bucket": classify_bucket(iso, settings),
            "has_subtitle": False,
            "subtitle_path": None,
        })
    log.info("[%s] 당일 영상 %d개", channel["name"], len(results))
    return results


def _download_subtitle(video: dict, subs_dir: Path, settings: dict) -> None:
    """ko 자동생성 자막 다운로드 → subs_dir. 성공 시 video 갱신."""
    langs = settings["youtube"]["subtitle_langs"]
    outtmpl = str(subs_dir / f"{video['id']}.%(ext)s")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt",
        "outtmpl": outtmpl,
        "ignoreerrors": True,
        "ignore_no_formats_error": True,
    }
    _apply_yt_opts(ydl_opts, settings)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([video["url"]])
    except Exception as e:  # noqa: BLE001
        log.warning("자막 다운로드 실패 %s: %s", video["id"], e)

    # 저장된 vtt 탐색 (예: <id>.ko.vtt)
    vtt = None
    for lang in langs:
        cand = subs_dir / f"{video['id']}.{lang}.vtt"
        if cand.exists():
            vtt = cand
            break
    if vtt is None:
        matches = list(subs_dir.glob(f"{video['id']}*.vtt"))
        vtt = matches[0] if matches else None

    if vtt and vtt.exists():
        text = _clean_vtt(vtt.read_text(encoding="utf-8", errors="ignore"))
        if text.strip():
            txt_path = subs_dir / f"{video['id']}.txt"
            txt_path.write_text(text, encoding="utf-8")
            video["has_subtitle"] = True
            video["subtitle_path"] = str(txt_path)
            log.info("자막 확보 %s (%d자)", video["id"], len(text))
            return
    log.info("자막 없음 %s → Whisper 후보", video["id"])


def collect(date_str: str | None = None) -> dict:
    """전체 채널 수집 실행. videos.json 저장 후 dict 반환."""
    settings = load_settings()
    date_str = date_str or today_kst_str()
    ddir = data_dir_for(date_str, settings)
    subs_dir = ddir / "subs"
    subs_dir.mkdir(exist_ok=True)

    # 증분: 이전 실행에서 이미 자막 확보한 영상은 재다운로드하지 않고 재사용
    prev_by_id: dict[str, dict] = {}
    vpath = ddir / "videos.json"
    if vpath.exists():
        for v in read_json(vpath).get("videos", []):
            prev_by_id[v["id"]] = v

    all_videos: list[dict] = []
    reused = 0
    for channel in load_channels():
        vids = _list_today_videos(channel, settings, date_str)
        for v in vids:
            prev = prev_by_id.get(v["id"])
            if prev and prev.get("subtitle_path") and Path(prev["subtitle_path"]).exists():
                v["has_subtitle"] = True
                v["subtitle_path"] = prev["subtitle_path"]
                if prev.get("subtitle_source"):
                    v["subtitle_source"] = prev["subtitle_source"]
                reused += 1
            else:
                _download_subtitle(v, subs_dir, settings)
        all_videos.extend(vids)
    if reused:
        log.info("자막 재사용 %d개(기존 영상), 신규만 다운로드", reused)

    payload = {"date": date_str, "count": len(all_videos), "videos": all_videos}
    write_json(ddir / "videos.json", payload)
    log.info("수집 완료: 총 %d개 영상 → %s", len(all_videos), ddir / "videos.json")
    return payload


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    collect(d)
