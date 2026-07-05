# 매일 경제 유튜브 분석 → 1장 대시보드

7개 한국 경제/증시 유튜브 채널의 **당일 업로드**를 자동 수집·분석해,
개장전/중/후로 정리하고 언급 종목 펀더멘털을 붙인 **1장짜리 HTML 대시보드**를 매일 생성합니다.

## 파이프라인

```
1) collect_youtube  yt-dlp: 채널 당일 영상 + ko 자막
2) transcribe       자막 없는 영상 → faster-whisper 전사 (ffmpeg 필요)
3) analyze          Claude Opus: 영상별 요약(A4 1장 줄글 상세요약 포함) + 종합 인사이트
4) stocks           네이버금융 지수/시총/영업이익·PER (KRX 로그인 불필요)
5) build_dashboard  Jinja2 → output/dashboard_<date>.html  (대시보드 + 채널별 리포트 통합)
```

## 산출 페이지 (단일 HTML)
`output/dashboard_<date>.html` 한 파일에 두 영역이 상단 고정 네비로 연결됩니다.
- **① 종합 대시보드**: 코스피/코스닥·시총 TOP5·핵심 이슈·개장전중후 요약 카드·종목 펀더멘털 테이블.
- **② 채널별 상세 리포트**: 계정별 A4 약 1장 분량의 줄글 요약(채널명 헤더 + 개장전/중/후 배지 + 목차).
  브라우저 인쇄 시 `page-break`로 **계정당 1페이지**로 출력됩니다.

## 대상 채널
삼프로TV · 815머니톡 · 증시각도기TV · 시황맨TV · 머니인더트랩 · 경제사냥꾼 · 이효석아카데미
(수정: `config/channels.yaml`)

## 설치

```powershell
# 1. 가상환경
python -m venv venv
venv\Scripts\pip install -r requirements.txt

# 2. Claude API 키
copy .env.example .env
#   .env 를 열어 ANTHROPIC_API_KEY 입력

# 3. ffmpeg (Whisper 전사용, 무자막 영상 있을 때만 필요)
#   winget install Gyan.FFmpeg   또는  choco install ffmpeg
#   설치 후 새 터미널에서  ffmpeg -version  확인
```

## 실행

```powershell
# 전체 (오늘 KST 기준)
venv\Scripts\python -m src.pipeline

# 특정 날짜 / 옵션
venv\Scripts\python -m src.pipeline --date 2026-07-04
venv\Scripts\python -m src.pipeline --skip-whisper
venv\Scripts\python -m src.pipeline --only stocks     # 단계별 실행

# 배치 파일 (venv 자동 감지)
run.bat
```

결과: `output/dashboard_<날짜>.html` (기본으로 브라우저 자동 오픈).

## 매일 자동 실행 (작업 스케줄러)

```powershell
# 관리자 PowerShell
.\register_task.ps1                 # 매일 16:30
.\register_task.ps1 -Time "17:00"   # 시간 변경

Get-ScheduledTask -TaskName EconYoutubeDashboard   # 확인
Start-ScheduledTask -TaskName EconYoutubeDashboard # 수동 트리거
```

## 설정 (`config/settings.yaml`)
- `market.open/close`: 개장전/중/후 분류 기준 시각
- `whisper.enabled/model`: 전사 on/off, 정확도(속도 트레이드오프)
- `claude.model / summary_model`: 분석 모델 (비용 절감 시 summary_model 을 haiku/sonnet 로)
- `claude.max_transcript_chars`: 긴 자막 압축 임계값
- `stocks.aliases`: 구어체 종목명 → 정식명 사전
- `dashboard.open_after_build`: 빌드 후 자동 오픈

## 비용 참고
Opus + 장편 자막 다수는 일일 토큰 비용이 큽니다. 절감 방법:
- `config/channels.yaml` 에서 일부 채널 `enabled: false`
- `settings.yaml` 의 `summary_model` 을 저비용 모델로 분리
- `max_transcript_chars` 하향

## 산출물 구조
```
data/<date>/videos.json      수집 메타 + 장구분
data/<date>/subs/*.txt       자막/전사 텍스트
data/<date>/analysis.json    영상별 요약 + 종합
data/<date>/stocks.json      지수/시총/펀더멘털
output/dashboard_<date>.html 최종 대시보드
```

## 알려진 리스크
- **yt-dlp 차단**: 유튜브 정책 변화 시 `pip install -U yt-dlp` 또는 쿠키 옵션 필요.
- **네이버 재무 셀렉터 변동**: 파싱 실패 시 해당 필드는 `-` 로 공란 표기(파이프라인은 계속).
- **종목명 매핑 오탐**: 자동 추정이라 오차 가능 → `aliases` 사전으로 보정.
- **주말/휴장**: 지수·시총은 직전 영업일 기준으로 표기(대시보드에 배지 표시).
