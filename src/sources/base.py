"""
src/sources/base.py

뉴스/재무 데이터 소스의 추상 인터페이스 정의.
구체 어댑터(naver_news, dart)가 이 인터페이스를 구현한다.
코어는 특정 벤더(네이버·DART)를 직접 알지 않고 이 추상만 안다(원칙 2).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class NewsItem:
    """수집된 뉴스 1건. 현재 재료는 제목 + 스니펫뿐이다(본문 크롤링은 유예)."""

    title: str            # 기사 제목
    snippet: str          # 짧은 요약(스니펫)
    link: str             # 원문 링크
    published: str        # 발행 일시(문자열, 소스 포맷 그대로)
    source_index: int = field(default=-1)  # 배치 내 번호(수집 후 부여)


class NewsSource(ABC):
    """뉴스(제목 + 스니펫)를 가져오는 소스의 추상 인터페이스."""

    @abstractmethod
    def fetch(self, query: str, *, display: int = 30, sort: str = "date") -> list[NewsItem]:
        """검색어로 뉴스를 중립 수집한다. 부정 키워드를 붙이지 않는다(원칙 1)."""
        ...

    def fetch_broad(self, query: str, *, months: int = 12) -> list[NewsItem]:
        """
        회사명 하나로 최근 `months` 개월치를 '넓게' 중립 수집한다(수집 개수는 사용자가 고르지
        않는다 — 시스템 기본값). 부정 키워드를 붙이지 않는다(원칙 1). 정확도순+최신순을 함께
        훑고 페이지네이션으로 최대한 모은다. 어떤 것도 검색 단계에서 버리지 않는다 — 좁히기는
        이후 판단·표시가 한다(원칙 7).

        기본 구현은 구형 소스·페이크가 깨지지 않게 fetch 를 한 번 부른다(하위호환). 실제 벤더
        (네이버 등)는 이 메서드를 override 해 페이지네이션·기간 필터를 한다. 코어는 이 추상만
        안다(원칙 2).
        """
        return self.fetch(query)


class FinancialSource(ABC):
    """
    재무/공시 데이터를 가져오는 소스의 추상 인터페이스.
    코어는 특정 벤더(DART)를 직접 알지 않고 이 추상만 안다(원칙 2).

    두 가지를 제공한다:
    - fetch_account_groups: '계정과목 목록'(계정연결의 울타리 — account-linkage.md).
    - fetch_financials: '재무 규모 숫자'(규모 비율의 분모 후보 — magnitude-rules.md).
    코어는 어느 것이 어느 계정에서 왔는지 등 DART 내부를 알지 않는다(원칙 2).
    """

    @abstractmethod
    def fetch_account_groups(self, corp: str) -> list[str]:
        """회사의 계정과목(계정군) 후보 목록을 반환한다. 실패 시 임의값 말고 에러(원칙 4)."""
        ...

    @abstractmethod
    def fetch_financials(self, corp: str) -> dict:
        """
        규모 비율의 분모 후보를 반환한다: {"매출액": int, "자산총계": int, "자기자본": int}(원).
        확보 못 한 항목은 빠질 수 있다(그 분모로는 비율을 못 냄). 어느 분모로 잴지는 코어가
        아니라 판단(LLM)이 고른다 — 여기서는 '숫자'만 준다. 실패 시 임의값 말고 에러(원칙 4).
        """
        ...

    def fetch_account_statements(self, corp: str) -> list[dict]:
        """
        계정과목을 '재무제표 구분별'로 묶어 반환한다(화면의 계정 선택 표 구성용).
        반환: [{"sj_div": 구분코드, "sj_nm": 표이름, "accounts": [계정이름...]}, ...].

        이는 fetch_account_groups(울타리)와 '같은 계정집합'에 소스 원본의 구분·순서만 입힌
        표시용 뷰다 — 판단(계정 연결)에는 쓰지 않는다(그건 여전히 flat 울타리로 한다).
        기본 구현: 구분 정보가 없는 소스는 단일 그룹으로 준다. 재무제표 구분을 가진 소스
        (DART 등)는 이 메서드를 override 해 원본 구분/순서를 그대로 노출한다(임의 분류 금지 —
        원칙 1). 표시용이므로 abstract 가 아니라 안전한 기본값을 둔다(기존 구현 깨지 않음).
        """
        return [{"sj_div": "", "sj_nm": "계정과목", "accounts": self.fetch_account_groups(corp)}]
