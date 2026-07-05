"""4단계: 지수/등락률 + 시총 TOP5 + 언급종목 펀더멘털 (네이버금융 전용).

pykrx 는 2026년부터 KRX 로그인(KRX_ID/KRX_PW)을 요구해 무인 실행에 부적합 →
로그인 불필요한 네이버금융으로 통일.

데이터 소스:
- 지수:      m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/basic  (JSON)
- 시총랭킹:  finance.naver.com/sise/sise_market_sum.naver       (HTML, 종목명→코드/시총/현재가/등락/PER)
- 재무:      finance.naver.com/item/main.naver?code=            (영업이익/매출/PBR)

입력: data/<date>/analysis.json (언급종목)
출력: data/<date>/stocks.json
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict

import requests
from bs4 import BeautifulSoup

from .common import (data_dir_for, load_settings, log, now_kst, read_json,
                     today_kst_str, write_json)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
INDEX_API = "https://m.stock.naver.com/api/index/{}/basic"
MARKET_SUM = "https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
SOSOK = {"KOSPI": 0, "KOSDAQ": 1}

# 종목 스냅샷 캐시: code -> {name, price, change_pct, market_cap, per, market}
_snapshot: dict[str, dict] = {}
_name_to_code: dict[str, str] = {}


def _num(s: str | None) -> float | None:
    if not s:
        return None
    s = s.replace(",", "").replace("%", "").replace("+", "").strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _get_index(idx: str, timeout: int) -> dict:
    """지수 종가 + 등락률(부호 포함)."""
    try:
        j = requests.get(INDEX_API.format(idx), headers=HEADERS, timeout=timeout).json()
    except Exception as e:  # noqa: BLE001
        log.warning("지수 %s 조회 실패: %s", idx, e)
        return {"close": None, "change_pct": None}
    close = _num(j.get("closePrice"))
    ratio = _num(j.get("fluctuationsRatio"))
    # 방향 부호: compareToPreviousPrice.code (2=상승, 5=하락)
    direction = (j.get("compareToPreviousPrice") or {}).get("code")
    if ratio is not None and direction in ("4", "5"):
        ratio = -ratio
    return {"close": close, "change_pct": ratio}


def _parse_market_sum_page(sosok: int, page: int, timeout: int) -> list[dict]:
    url = MARKET_SUM.format(sosok=sosok, page=page)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.encoding = "euc-kr"
    except Exception as e:  # noqa: BLE001
        log.warning("시총랭킹 조회 실패 (sosok=%s p=%s): %s", sosok, page, e)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.select_one("table.type_2")
    if not table:
        return []
    rows = []
    market = "KOSPI" if sosok == 0 else "KOSDAQ"
    for tr in table.select("tbody tr"):
        a = tr.select_one("a.tltle")
        if not a:
            continue
        code = a["href"].split("code=")[-1]
        tds = [td.get_text(strip=True) for td in tr.select("td")]
        if len(tds) < 11:
            continue
        # tds: 0=N 1=종목명 2=현재가 3=전일비 4=등락률 5=액면가 6=시가총액(억) ... 10=PER
        cap_eok = _num(tds[6])
        rows.append({
            "code": code,
            "name": a.get_text(strip=True),
            "price": _num(tds[2]),
            "change_pct": _num(tds[4]),
            "market_cap": int(cap_eok * 10**8) if cap_eok else None,  # 억원 → 원
            "per": _num(tds[10]),
            "market": market,
        })
    return rows


def _build_index(settings) -> None:
    """시총랭킹 상위 N페이지 스캔 → 종목명→코드 + 스냅샷 캐시."""
    if _snapshot:
        return
    pages = settings["stocks"].get("marketsum_pages", 20)
    timeout = settings["stocks"]["request_timeout_sec"]
    for market, sosok in SOSOK.items():
        for p in range(1, pages + 1):
            rows = _parse_market_sum_page(sosok, p, timeout)
            if not rows:
                break
            for row in rows:
                _snapshot.setdefault(row["code"], row)
                _name_to_code.setdefault(row["name"], row["code"])
            time.sleep(0.1)
    log.info("종목 인덱스 구축: %d 종목", len(_snapshot))


def _resolve_code(name: str, aliases: dict) -> str | None:
    if not name:
        return None
    name = name.strip()
    canon = aliases.get(name, name)
    if canon in _name_to_code:
        return _name_to_code[canon]
    for k, code in _name_to_code.items():
        if canon and (canon in k or k in canon):
            return code
    return None


def _top5(market: str, n: int, timeout: int) -> list[dict]:
    rows = _parse_market_sum_page(SOSOK[market], 1, timeout)  # 1페이지 = 시총 상위
    return rows[:n]


def _fundamentals(code: str, settings) -> dict:
    """네이버 종목 메인: 영업이익/매출액(억원, 최근 연간 실적) + PBR + 기준연도.

    기업실적분석 표는 [연간 4열 + 분기 6열] 구조. 연간 열 중 추정치'(E)'를 제외한
    가장 최근 연도 값을 채택.
    """
    import re
    out = {"영업이익": None, "매출액": None, "pbr": None, "재무기준": None}
    url = settings["stocks"]["naver_finance_base"] + code
    timeout = settings["stocks"]["request_timeout_sec"]
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.encoding = "utf-8"  # item/main.naver 는 UTF-8 (sise 페이지만 euc-kr)
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:  # noqa: BLE001
        log.warning("재무 크롤 실패 %s: %s", code, e)
        return out

    el = soup.select_one("#_pbr")
    if el:
        out["pbr"] = _num(el.text)

    table = soup.select_one("div.cop_analysis table")
    if not table:
        return out

    # 연간 열 개수 (그룹헤더 '연간' colspan)
    num_annual = 4
    for th in table.select("thead th"):
        if "연간" in th.get_text() and th.get("colspan"):
            num_annual = int(th["colspan"])
            break

    # 기간 라벨 행 (YYYY.MM 패턴 포함하는 thead tr)
    date_labels: list[str] = []
    for tr in table.select("thead tr"):
        texts = [th.get_text(strip=True) for th in tr.select("th")]
        if any(re.match(r"\d{4}\.\d{2}", x) for x in texts):
            date_labels = texts
            break

    # 연간 열 중 추정치(E) 제외한 가장 최근 열 인덱스
    target = None
    for idx in range(min(num_annual, len(date_labels))):
        lab = date_labels[idx]
        if re.match(r"\d{4}\.\d{2}", lab) and "(E)" not in lab:
            target = idx
            out["재무기준"] = lab
    if target is None:
        target = 0

    for tr in table.select("tbody tr"):
        th = tr.select_one("th")
        if not th:
            continue
        label = th.get_text(strip=True)
        if label not in ("영업이익", "매출액"):
            continue
        tds = [td.get_text(strip=True) for td in tr.select("td")]
        if target < len(tds):
            out[label] = _num(tds[target])  # 억원
    return out


def build_stocks(date_str: str | None = None) -> dict:
    settings = load_settings()
    date_str = date_str or today_kst_str()
    ddir = data_dir_for(date_str, settings)
    timeout = settings["stocks"]["request_timeout_sec"]
    n = settings["stocks"]["top_n_marketcap"]

    _build_index(settings)

    # 지수 등락률로 오늘 개장 여부 추정(주말/휴장이면 직전 영업일 데이터를 네이버가 그대로 제공)
    base = now_kst().strftime("%Y-%m-%d")
    weekday = now_kst().weekday()  # 5=토,6=일
    result = {
        "date": date_str,
        "base_date": base,
        "is_trading_day": weekday < 5,
        "indices": {
            "kospi": _get_index("KOSPI", timeout),
            "kosdaq": _get_index("KOSDAQ", timeout),
        },
        "top5": {
            "kospi": _top5("KOSPI", n, timeout),
            "kosdaq": _top5("KOSDAQ", n, timeout),
        },
        "mentioned": [],
    }

    # 언급종목 집계
    analysis_path = ddir / "analysis.json"
    mentions: Counter = Counter()
    channels_by_name: dict[str, set] = defaultdict(set)
    dir_by_name: dict[str, list] = defaultdict(list)
    if analysis_path.exists():
        for v in read_json(analysis_path).get("videos", []):
            for m in v.get("언급종목", []):
                nm = (m.get("name") or "").strip()
                if not nm:
                    continue
                mentions[nm] += 1
                channels_by_name[nm].add(v["channel"])
                if m.get("방향성"):
                    dir_by_name[nm].append(m["방향성"])

    aliases = settings["stocks"].get("aliases", {})
    for name, count in mentions.most_common():
        code = _resolve_code(name, aliases)
        snap = _snapshot.get(code, {}) if code else {}
        entry = {
            "name": name, "code": code,
            "market": snap.get("market"),
            "market_cap": snap.get("market_cap"),
            "price": snap.get("price"),
            "change_pct": snap.get("change_pct"),
            "per": snap.get("per"),
            "영업이익": None, "매출액": None, "pbr": None,
            "mention_count": count,
            "channels": sorted(channels_by_name[name]),
            "방향성": Counter(dir_by_name[name]).most_common(1)[0][0] if dir_by_name[name] else None,
        }
        if code:
            entry.update(_fundamentals(code, settings))
        result["mentioned"].append(entry)

    write_json(ddir / "stocks.json", result)
    log.info("종목 데이터 완료: 언급 %d종목 → %s", len(result["mentioned"]), ddir / "stocks.json")
    return result


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    build_stocks(d)
