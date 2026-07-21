"""
tests/invariants/_harness.py

불변식 테스트 공용 도구. 케이스를 '사건 서사'(제목+스니펫)로 만들어 실제 판단
엔진(judge_items)에 태우고, 심각도 정렬(rank_by_severity)까지 돌린 결과를 돌려준다.

설계 원칙:
- 케이스는 실존 기업이 아닌 '가상 기업'으로 쓴다(검증 지시).
- 키워드 나열이 아니라 사건 서사로 쓴다. 판단은 LLM이 맥락을 이해해 내려야 하며,
  단어 매칭으로 통과하면 안 된다(원칙 1).
- 불변식은 '절대 점수'가 아니라 '상대 순위/상태'로 단언한다. severity_rank는 LLM의
  홀리스틱 신호라 값 자체는 흔들릴 수 있으나, 순위 관계·규모 상태는 성립해야 한다.

주의: 이 모듈은 실제 Anthropic API를 호출한다(과금). conftest가 .env 키를 주입한다.
키가 없으면 judge_items가 JudgeConfigError로 멈추고, 그 경우 테스트는 skip 대신
실패로 두어 '검증을 못 했다'는 사실이 드러나게 한다.
"""

from dataclasses import dataclass

from src.judge.engine import judge_items
from src.judge.schema import Judgment
from src.ranking import rank_by_severity
from src.sources.base import NewsItem


@dataclass
class Case:
    """가상 사건 서사 1건."""

    key: str        # 사람이 읽는 식별자(리포트용)
    title: str
    snippet: str


# 가상 기업은 DART에 없으므로, 전형적 제조업 재무제표 계정군을 합성해 '울타리'로 쓴다.
# (실제 회사 맞춤이 아니라 일반적 계정군 — 원칙 2. 계정 연결 검증 전용 재료.)
SAMPLE_ACCOUNTS = [
    "현금및현금성자산", "매출채권및기타채권", "대손충당금", "재고자산", "유형자산",
    "무형자산", "영업권", "관계기업투자", "이연법인세자산", "매입채무및기타채무",
    "단기차입금", "장기차입금", "충당부채", "우발부채", "순확정급여부채",
    "자본금", "이익잉여금", "기타포괄손익누계액", "매출액", "매출원가",
    "판매비와관리비", "기타수익", "기타비용", "금융수익", "금융비용", "법인세비용",
]


def judge_cases(company: str, cases: list[Case],
                accounts: list[str] | None = None,
                financials: dict | None = None) -> tuple[dict[str, Judgment], list[Judgment]]:
    """
    케이스들을 한 배치로 판단하고, (key→Judgment, 심각도순 리스트)를 돌려준다.
    source_index는 cases 순서로 부여하고, key와 매핑해 돌려준다.
    accounts: DART 계정 후보(울타리). 가상 기업은 DART에 없으므로, 계정 연결 검증 때
      전형적인 계정군 목록을 합성해 넘긴다(엔진의 enforce_account_fence가 이걸로 집행).
    financials: DART 재무 규모(분모 후보). 규모 앵커 검증 때 합성 재무숫자를 넘긴다
      (엔진의 apply_magnitude_anchor가 이걸로 비율을 계산). 없으면 비율은 계산 안 됨.
    """
    items = [
        NewsItem(title=c.title, snippet=c.snippet, link="", published="2026-07-10", source_index=i)
        for i, c in enumerate(cases)
    ]
    judgments = judge_items(company, items, accounts, financials)
    by_idx = {j.source_index: j for j in judgments}

    missing = [c.key for i, c in enumerate(cases) if i not in by_idx]
    assert not missing, f"엔진이 일부 뉴스를 누락했다(원칙: 누락 금지). 누락 key={missing}"

    by_key = {cases[j.source_index].key: j for j in judgments}
    ranked = rank_by_severity(judgments)
    return by_key, ranked


def _norm(text: str) -> str:
    """공백 제거 정규화(근거 추적 비교용). LLM 발췌가 띄어쓰기만 다를 수 있어 최소 정리."""
    return "".join(text.split())


def quote_traceable(quote: str, case: Case) -> bool:
    """
    반날조 구조 검사: 근거 문장(quote)이 원문(제목+스니펫)에서 실제로 추적되는가.
    공백만 정규화한 뒤 부분문자열 여부로 본다. 원문에 없는 문장을 근거로 대면 날조다.
    (짧은 우연 일치를 막기 위해 최소 길이 6자 이상만 신뢰.)
    """
    q = _norm(quote)
    if len(q) < 6:
        return False
    src = _norm(case.title + " " + case.snippet)
    return q in src


def rank_position(ranked: list[Judgment], source_index: int) -> int:
    """정렬 리스트에서 해당 뉴스의 1-based 위치(작을수록 위/심각)."""
    for pos, j in enumerate(ranked, start=1):
        if j.source_index == source_index:
            return pos
    raise AssertionError(f"source_index={source_index} 가 정렬 결과에 없다")


def fmt(j: Judgment) -> str:
    """리포트용 한 줄 요약(실물 출력)."""
    m = j.magnitude
    return (
        f"rank={j.severity_rank} relevant={j.relevant} dir={j.direction} "
        f"stage={j.stage} intrinsic={j.intrinsic_risk} "
        f"mag={m.size}/{m.certainty} abst={j.abstained} ev={len(j.evidence)} "
        f"| {j.one_line_reason}"
    )
