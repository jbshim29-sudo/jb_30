"""2단계: 자막 없는 영상 → 오디오 추출 → faster-whisper 전사.

videos.json 을 읽어 has_subtitle=False 인 영상만 처리.
성공 시 subtitle_path 를 전사 텍스트로 채우고 has_subtitle=True(source='whisper') 갱신.
ffmpeg 필요. 미설치/실패 시 해당 영상은 제목·설명 폴백(analyze 단계에서 처리).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from yt_dlp import YoutubeDL

from .common import (data_dir_for, load_settings, log, read_json,
                     today_kst_str, write_json)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _download_audio(url: str, out_dir: Path, vid: str) -> Path | None:
    """bestaudio 다운로드 → m4a/webm. 경로 반환."""
    outtmpl = str(out_dir / f"{vid}.%(ext)s")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "ignoreerrors": True,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:  # noqa: BLE001
        log.warning("오디오 다운로드 실패 %s: %s", vid, e)
        return None
    matches = list(out_dir.glob(f"{vid}.*"))
    return matches[0] if matches else None


def _load_model(settings: dict):
    from faster_whisper import WhisperModel
    w = settings["whisper"]
    log.info("Whisper 모델 로드: %s (%s/%s)", w["model"], w["device"], w["compute_type"])
    return WhisperModel(w["model"], device=w["device"], compute_type=w["compute_type"])


def transcribe(date_str: str | None = None) -> dict:
    settings = load_settings()
    date_str = date_str or today_kst_str()
    ddir = data_dir_for(date_str, settings)
    videos_path = ddir / "videos.json"
    if not videos_path.exists():
        log.warning("videos.json 없음 → 수집 먼저 필요")
        return {"videos": []}

    payload = read_json(videos_path)
    videos = payload.get("videos", [])
    pending = [v for v in videos if not v.get("has_subtitle")]

    if not settings["whisper"].get("enabled", True):
        log.info("Whisper 비활성화 → 전사 생략 (%d개 무자막)", len(pending))
        return payload
    if not pending:
        log.info("무자막 영상 없음 → 전사 생략")
        return payload
    if not _ffmpeg_available():
        log.warning("ffmpeg 미설치 → Whisper 전사 생략. %d개 영상은 제목·설명 폴백", len(pending))
        return payload

    max_dur = settings["whisper"]["max_duration_sec"]
    audio_dir = ddir / "audio_tmp"
    audio_dir.mkdir(exist_ok=True)
    subs_dir = ddir / "subs"
    subs_dir.mkdir(exist_ok=True)

    model = None
    for v in pending:
        dur = v.get("duration") or 0
        if dur and dur > max_dur:
            log.info("길이 초과(%ds>%ds) 전사 생략 %s", dur, max_dur, v["id"])
            continue
        audio = _download_audio(v["url"], audio_dir, v["id"])
        if not audio:
            continue
        if model is None:
            model = _load_model(settings)
        log.info("전사 시작 %s ...", v["id"])
        try:
            segments, _ = model.transcribe(str(audio), language="ko", vad_filter=True)
            text = "\n".join(seg.text.strip() for seg in segments if seg.text.strip())
        except Exception as e:  # noqa: BLE001
            log.warning("전사 실패 %s: %s", v["id"], e)
            text = ""
        finally:
            try:
                audio.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        if text.strip():
            txt_path = subs_dir / f"{v['id']}.txt"
            txt_path.write_text(text, encoding="utf-8")
            v["has_subtitle"] = True
            v["subtitle_path"] = str(txt_path)
            v["subtitle_source"] = "whisper"
            log.info("전사 완료 %s (%d자)", v["id"], len(text))

    # 임시 오디오 폴더 정리
    try:
        shutil.rmtree(audio_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    write_json(videos_path, payload)
    return payload


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    transcribe(d)
