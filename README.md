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

## 자동 실행 — ⭐ 권장: GitHub Actions (PC를 꺼도 동작)

로컬 작업 스케줄러는 **PC가 켜져 있을 때만** 돕니다. 컴퓨터를 꺼도 갱신하려면
클라우드에서 도는 **GitHub Actions**를 사용하세요(`.github/workflows/daily.yml`).

- **스케줄:** 평일(월~금) KST **06~23시 매시간**(하루 18회). cron 은 UTC 기준 2줄로 분할
  (`0 0-14 * * 1-5` + `0 21-23 * * 0-4`)되어 날짜경계까지 정확히 커버.
- 매 실행: 파이프라인 → `public/index.html` 갱신 → 자동 커밋·푸시 → **Vercel 자동 재배포**.

### AI 분석 없이도 자동 동작 (API 키 선택사항)
- **`ANTHROPIC_API_KEY`가 없으면** → **무분석 모드**로 동작: 코스피/코스닥·시총 TOP5 +
  채널별 당일 영상 목록(제목·시각·링크·설명)만 표시. **시크릿 설정 불필요, 완전 자동.**
- **키를 등록하면** → 다음 실행부터 영상 요약·시장전망·언급종목 분석이 자동으로 켜지고,
  데이터 캐시로 새 영상만 분석해 비용을 최소화(증분 분석).
  - 등록: Settings → Secrets and variables → Actions → **Secrets 탭** → New repository secret
    → Name `ANTHROPIC_API_KEY` (※ "Environments"가 아니라 "Secrets"에 넣어야 함)

**설정 (푸시 권한만 필수):**
- Settings → Actions → General → Workflow permissions → **Read and write** 체크. (완료됨)
- 수동 실행: Actions 탭 → 워크플로 선택 → **Run workflow**.

> cron은 러너 부하에 따라 수 분 지연될 수 있습니다(정시 보장 아님).
> yt-dlp가 클라우드(GitHub) IP에서 차단될 수 있으니 첫 실행 로그를 확인하세요.

## (선택) 로컬 실행 — 작업 스케줄러

PC가 항상 켜져 있는 환경이면 로컬로도 가능합니다(Actions와 병행 시 커밋 충돌 주의 → 하나만 사용 권장).

```powershell
# 관리자 PowerShell
.\register_task.ps1                 # 매일 16:30
.\register_task.ps1 -Time "17:00"   # 시간 변경
Get-ScheduledTask -TaskName EconYoutubeDashboard   # 확인
```

## 웹 배포 (Vercel · 정적 호스팅)

이 프로젝트는 **웹 서버가 아니라 정적 HTML을 생성하는 배치 파이프라인**입니다.
Vercel에서 유튜브 수집·Whisper·Claude 분석을 직접 돌릴 수 없으므로(무거운 의존성·API 키·장중 데이터),
**로컬에서 파이프라인이 생성한 `public/index.html`을 Vercel이 정적으로 호스팅**하는 구조입니다.

- 빌드 시 최신 대시보드가 `public/index.html`로 미러링됩니다.
- `vercel.json`이 `@vercel/static`으로 정적 배포를 강제 → 파이썬 런타임 감지(엔트리포인트 에러) 회피.
- Vercel 프로젝트 설정: **Framework Preset = Other**, Build Command/Output 은 `vercel.json`이 처리하므로 비워도 됩니다.

**매일 라이브 갱신 흐름:**
```
로컬 스케줄러 → 파이프라인 실행 → public/index.html 갱신 → git commit & push → Vercel 자동 재배포
```
자동 커밋·푸시를 원하면 `run.bat` 마지막에 아래를 추가하세요(원격 인증 필요):
```bat
git add public/index.html && git commit -m "update %date%" && git push
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
