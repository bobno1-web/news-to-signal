"""
src/sources/naver_news.py

네이버 뉴스 검색 API 어댑터. NewsSource 구현.
중립 수집: 검색어에 부정 키워드를 붙이지 않는다(원칙 1). 회사명만으로 검색하고,
악재 우선은 이후 심각도 정렬 단계에서 처리한다(decision-log D2).
현재 재료는 제목 + 스니펫(description)뿐이다(본문 크롤링은 유예).

넓은 수집(fetch_broad): 최근 1년치를 정확도순+최신순으로 페이지네이션해 최대한 모은다.
수집 개수는 사용자가 고르지 않는다 — 시스템 기본값이다(루프6b). 네이버 검색 API 는
날짜 범위 파라미터가 없어 '기간 창 분할'을 직접 못 하므로, 최신순 페이지네이션 + 클라이언트
쪽 날짜 필터로 근사하고, 네이버의 1000건 상한에 걸리면 숨기지 않고 표기한다(원칙 7).
"""

import html
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from src.sources.base import NewsItem, NewsSource

_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")

_PAGE = 100          # 네이버 한 페이지 최대 건수
_START_MAX = 1000    # 네이버 start 상한(그 너머는 못 가져온다)
_DATE_PAGES = 10     # 최신순 최대 페이지(=최대 1000건)
_SIM_PAGES = 3       # 정확도순 추가 페이지(오래됐지만 관련성 높은 것 보강)


class NaverConfigError(RuntimeError):
    """API 키 미설정 등 설정 오류. 임의 값으로 대체하지 않고 즉시 멈춘다(원칙 4)."""


class NaverRateLimited(RuntimeError):
    """네이버 호출 제한(초당/일일). 코드 결함이 아니라 정상 동작으로 간주한다."""


def _clean(text: str) -> str:
    """네이버가 주는 <b> 태그·HTML 엔티티를 제거한다(텍스트 정리, 판단 아님)."""
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _parse_pubdate(s: str) -> datetime | None:
    """네이버 pubDate(RFC822)를 tz-aware datetime 으로. 실패하면 None(버리지 않기 위함)."""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class NaverNewsSource(NewsSource):
    """네이버 뉴스 검색 어댑터."""

    def __init__(self, *, max_retries: int = 3, backoff: float = 1.5) -> None:
        self._client_id = os.environ.get("NAVER_CLIENT_ID")
        self._client_secret = os.environ.get("NAVER_CLIENT_SECRET")
        self._max_retries = max_retries
        self._backoff = backoff
        # 넓은 수집의 마지막 실행 통계(투명성·보고용 — 판단에는 쓰지 않는다).
        self.last_broad_meta: dict = {}

    def _headers(self) -> dict:
        if not self._client_id or not self._client_secret:
            raise NaverConfigError(
                "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다. "
                "임의 값으로 대체하지 않고 중단합니다(원칙 4)."
            )
        return {
            "X-Naver-Client-Id": self._client_id,
            "X-Naver-Client-Secret": self._client_secret,
        }

    def _search(self, query: str, *, display: int, sort: str, start: int) -> tuple[list[NewsItem], int]:
        """네이버에 한 페이지 요청. (뉴스 리스트, total) 반환. rate limit 은 재시도한다."""
        headers = self._headers()
        params = {"query": query, "display": display, "sort": sort, "start": start}

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            resp = requests.get(_ENDPOINT, headers=headers, params=params, timeout=10)
            if resp.status_code == 429:
                last_exc = NaverRateLimited(f"429 (attempt {attempt + 1})")
                time.sleep(self._backoff * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise NaverRateLimited(f"네이버 응답 {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            return self._parse(payload), int(payload.get("total", 0) or 0)

        raise last_exc or NaverRateLimited("알 수 없는 rate limit 상태")

    def fetch(self, query: str, *, display: int = 30, sort: str = "date") -> list[NewsItem]:
        """
        회사명(query)으로 뉴스를 중립 수집한다(한 페이지).
        display: 가져올 건수(네이버 최대 100). sort: 'date'(최신) | 'sim'(정확도).
        넓은 수집은 fetch_broad 를 쓴다 — 이 메서드는 하위호환·단발 조회용이다.
        """
        return self._search(query, display=min(display, _PAGE), sort=sort, start=1)[0]

    def _collect_sorted(self, query: str, sort: str, *, max_pages: int,
                        cutoff: datetime | None) -> tuple[list[NewsItem], bool]:
        """한 정렬 기준으로 페이지네이션 수집. cutoff 가 있고(최신순) 한 페이지가 통째로
        cutoff 보다 오래됐으면 조기 종료한다. (수집리스트, 상한도달여부) 반환."""
        collected: list[NewsItem] = []
        hit_ceiling = False
        for page in range(max_pages):
            start = 1 + page * _PAGE
            if start > _START_MAX:
                hit_ceiling = True
                break
            page_items, total = self._search(query, display=_PAGE, sort=sort, start=start)
            collected.extend(page_items)
            if len(page_items) < _PAGE:
                break                                   # 더 없음
            if start + _PAGE > _START_MAX and total > _START_MAX:
                hit_ceiling = True                      # 아직 더 있는데 네이버 상한에 걸림
                break
            if cutoff is not None and page_items:        # 최신순: 페이지가 통째로 오래됐으면 멈춤
                dates = [_parse_pubdate(it.published) for it in page_items]
                dated = [d for d in dates if d is not None]
                if dated and all(d < cutoff for d in dated):
                    break
            time.sleep(0.1)                              # 예의상 간격(429 완화)
        return collected, hit_ceiling

    def fetch_broad(self, query: str, *, months: int = 12) -> list[NewsItem]:
        """
        최근 `months` 개월치를 넓게 중립 수집한다(회사명 단일 검색어, 정확도순+최신순).
        - 최신순(date)으로 최대 10페이지(≈1000건) + 정확도순(sim) 3페이지 보강.
        - 원문 링크(URL) 기준으로 '동일 기사'만 literal 중복 제거(의미 판단 아님 — 원칙 1 안전).
        - 최근 months 개월 밖은 제외하되, 날짜 파싱 실패분은 버리지 않는다(원칙 7).
        - 네이버 1000건 상한에 걸리면 last_broad_meta 에 표기(숨기지 않음).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(round(months * 30.44)))

        date_items, ceil_d = self._collect_sorted(query, "date", max_pages=_DATE_PAGES, cutoff=cutoff)
        sim_items, ceil_s = self._collect_sorted(query, "sim", max_pages=_SIM_PAGES, cutoff=None)

        raw = date_items + sim_items
        # literal 중복 제거: 같은 원문 URL 은 같은 기사다(의미 병합 아님).
        by_url: dict[str, NewsItem] = {}
        dup_removed = 0
        for it in raw:
            k = (it.link or "").strip() or f"__no_url__{len(by_url)}_{id(it)}"
            if k in by_url:
                dup_removed += 1
                continue
            by_url[k] = it
        merged = list(by_url.values())

        # 기간 필터: 파싱 가능한 것만 cutoff 로 거르고, 파싱 실패분은 보존한다(숨기지 않음).
        in_window: list[NewsItem] = []
        undated = 0
        for it in merged:
            d = _parse_pubdate(it.published)
            if d is None:
                undated += 1
                in_window.append(it)
            elif d >= cutoff:
                in_window.append(it)

        self.last_broad_meta = {
            "months": months,
            "raw_fetched": len(raw),
            "date_pages_items": len(date_items),
            "sim_pages_items": len(sim_items),
            "url_dups_removed": dup_removed,
            "after_url_dedup": len(merged),
            "dropped_older_than_window": len(merged) - len(in_window),
            "undated_kept": undated,
            "collected": len(in_window),
            "hit_naver_ceiling": bool(ceil_d or ceil_s),
        }
        return in_window

    @staticmethod
    def _parse(payload: dict) -> list[NewsItem]:
        items: list[NewsItem] = []
        for row in payload.get("items", []):
            items.append(
                NewsItem(
                    title=_clean(row.get("title", "")),
                    snippet=_clean(row.get("description", "")),
                    link=row.get("originallink") or row.get("link", ""),
                    published=row.get("pubDate", ""),
                )
            )
        return items
