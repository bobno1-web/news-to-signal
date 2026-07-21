"""
src/pipeline.py

triage 파이프라인 오케스트레이터: 회사명 1개 → 뉴스 중립 수집 → (DART 계정 후보) →
LLM 판단(+계정 연결 가설) → 3바구니 라우팅 → 1·2층 텍스트 출력.

이번 루프 스코프: evidence 강제(원칙 3 방어선), 계정 연결(가설+근거+울타리),
3바구니 라우팅. 제외(이후 루프): 재무숫자 기반 규모 비율 계산, 본문 크롤링, 웹 UI.

실행:  python -m src.pipeline "회사명" [수집건수] [담당계정군]
키(NAVER_CLIENT_ID/SECRET, ANTHROPIC_API_KEY, OPENDART_API_KEY)가 없으면 명확한
에러로 멈춘다(원칙 4). 키는 셸 환경변수 또는 프로젝트 루트 .env 에서 읽는다
(값은 코드에 심지 않는다). 코어는 네이버·DART 를 직접 알지 않고 인터페이스 뒤에서
받는다(원칙 2 — NewsSource / FinancialSource).
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.judge.engine import judge_items
from src.judge.prescreen import prescreen
from src.judge.schema import Judgment
from src.ranking import Basket, classify_and_rank
from src.sources.base import FinancialSource, NewsItem, NewsSource
from src.sources.dart import DartSource
from src.sources.naver_news import NaverNewsSource

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
_ENV_LOADED = False


def _load_dotenv() -> None:
    """
    프로젝트 루트의 .env 를 프로세스 환경변수로 올린다(로컬 개발 표준 관행).
    - 키 값을 코드에 심지 않는다 — 값은 여전히 .env/환경변수에만 존재한다.
    - 이미 셸에 있는 값은 덮지 않는다(실제 환경 우선, override=False).
    - .env 가 없어도 조용히 넘어간다 → 이후 키 검사가 원칙 4대로 명확히 멈춘다.
    한 번만 파싱한다(idempotent).
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    if not _ENV_PATH.exists():
        return
    for raw in _ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)  # 셸 환경이 우선, .env 는 보충


@dataclass
class RunResult:
    """한 회사 분석의 전체 산출물. 수집 원본(items)은 전부 보존되고, 판단 대상(judgments)은
    깔때기를 통과한 대표들뿐이다. 접힌 것(prejudge_folded)도 삭제 없이 함께 넘긴다(원칙 7)."""

    company: str
    items: list[NewsItem]                       # 넓게 수집한 원본 전체(source_index 부여됨)
    accounts: list[str]
    financials: dict
    statements: list[dict]
    baskets: list[Basket]                        # 판단된 대표들의 3바구니 라우팅
    judgments: list[Judgment]                    # Opus 풀판단 결과(대표만)
    prejudge_folded: list = field(default_factory=list)   # [(NewsItem, 사유)] 중복·간이무관(접근가능)
    funnel: dict = field(default_factory=dict)   # 수집→중복정리→스크리닝→판단 단계 카운트


def run(company: str, *, viewer_account: str | None = None, months: int = 12,
        news_source: NewsSource | None = None, fin_source: FinancialSource | None = None,
        on_usage=None) -> RunResult:
    """
    회사명 하나로 최근 `months` 개월치 뉴스를 '넓게' 수집한 뒤, 판단 앞단 깔때기
    (중복 정리 → 간이 관련성 관문)를 거쳐 대표만 Opus 7축 판단으로 보내고, 라우팅한다.
    - 수집 개수는 사용자가 고르지 않는다(시스템 기본값, 루프6b).
    - 아무것도 삭제하지 않는다 — 접힌 것은 prejudge_folded 로 보존한다(원칙 7).
    - 코어는 벤더를 직접 알지 않는다 — NewsSource/FinancialSource 인터페이스로만 받는다(원칙 2).
    - on_usage: 선택적 텔레메트리(비용 측정). 판단 로직에는 영향 없다.
    """
    _load_dotenv()  # 키 검사(원칙 4)보다 먼저 .env 를 환경으로 올린다
    news_source = news_source or NaverNewsSource()
    fin_source = fin_source or DartSource()

    # 1) 넓은 수집(회사명 단일 검색어, 정확도순+최신순, 페이지네이션, ~1년). 개수 상한 없음.
    items = news_source.fetch_broad(company, months=months)
    for i, item in enumerate(items):
        item.source_index = i  # 배치 번호 부여(수집 원본 전체에 대해)

    # DART 계정 후보(울타리) + 재무 규모(분모). 확보 실패는 임의값 없이 예외로 멈춘다(원칙 4).
    accounts = fin_source.fetch_account_groups(company)
    financials = fin_source.fetch_financials(company)
    statements = fin_source.fetch_account_statements(company)  # 표시용(재무제표 구분·순서), 같은 캐시 재사용

    # 2) 깔때기: 중복 정리 + 간이 관련성 관문(느슨). 대표만 다음 단계로, 나머지는 접힘(보존).
    pre = prescreen(company, items, on_usage=on_usage)

    # 3) Opus 7축 풀판단 — 깔때기를 통과한 대표들에 대해서만(판단 로직은 불변).
    judgments = judge_items(company, pre.survivors, accounts, financials, on_usage=on_usage)
    baskets = classify_and_rank(judgments, viewer_account)

    # 증발 방어 정합(루프9): judge_items 가 대표(survivors) 전부에 정확히 1건을 보장하므로
    # judged == survivors 가 항상 성립한다(누락은 '미판단'으로 보존됨). 둘을 함께 노출해
    # 증발이 있으면 드러나게 한다(원칙 7). unjudged = 그중 판단 응답 누락으로 강제기권된 수.
    unjudged = sum(1 for j in judgments if j.unjudged)
    funnel = {
        "collected": len(items),                                      # 수집 N
        "after_dedup": pre.counts.get("after_dedup", len(items)),     # 중복정리 후
        "dup_folded": pre.counts.get("dup_folded", 0),               # 중복으로 접힘
        "irrelevant_folded": pre.counts.get("irrelevant_folded", 0),  # 간이 무관으로 접힘
        "truncated": pre.counts.get("truncated", 0),                 # 수집 상한 초과로 보류
        "survivors": len(pre.survivors),                             # 판단에 보낸 대표 수
        "judged": len(judgments),                                     # 돌아온 판단 수(=survivors, 보존)
        "unjudged": unjudged,                                        # 그중 판단 누락(미판단) 보존 수
        "collection_meta": getattr(news_source, "last_broad_meta", {}),
    }
    return RunResult(company=company, items=items, accounts=accounts, financials=financials,
                     statements=statements, baskets=baskets, judgments=judgments,
                     prejudge_folded=pre.folded, funnel=funnel)


def _fmt_pct(p: float) -> str:
    if p >= 1:
        return f"약 {p:.1f}%"
    if p >= 0.01:
        return f"약 {p:.2f}%"
    return "0.01% 미만"


def _fmt_won(v: int) -> str:
    조, 억 = 1_000_000_000_000, 100_000_000
    if v >= 조:
        return f"{v / 조:.1f}조원"
    if v >= 억:
        return f"{v / 억:.0f}억원"
    return f"{v:,}원"


def _magnitude_text(m) -> str:
    if m.ratio_pct is not None:  # 코드가 계산한 비율 앵커
        return f"규모 {m.size} · {m.denominator} 대비 {_fmt_pct(m.ratio_pct)}"
    if m.size == "미상":
        return "규모 미상(금액 불명)"
    if m.size == "해당없음":
        return "규모 해당없음"
    return f"규모 {m.size}"


def _account_tag(j) -> str:
    if j.account_links:
        groups = " / ".join(dict.fromkeys(link.account_group for link in j.account_links))
        return f"계정 {groups}"
    if j.account_abstained:
        return "계정 특정 불가(일반 리스크)"
    return "계정 미배정"


def _financials_line(financials: dict) -> str:
    if not financials:
        return "재무 규모: (확보 실패 — 비율 미계산)"
    parts = [f"{k} {_fmt_won(financials[k])}" for k in ("매출액", "자산총계", "자기자본") if k in financials]
    return "재무 규모(분모): " + " · ".join(parts)


def render(company: str, items: list[NewsItem], accounts: list[str], financials: dict,
           baskets: list[Basket], viewer_account: str | None = None) -> str:
    """
    3바구니 × (1층 제목·방향·규모(비율)·계정·한줄이유 + 2층 근거문장·규모근거·계정가설)을 렌더.
    내부값(severity_rank)은 노출하지 않는다(원칙 5). 어떤 뉴스도 삭제하지 않는다(원칙 7).
    """
    by_index = {it.source_index: it for it in items}
    total = sum(len(b.items) for b in baskets)
    lines = [
        f"[{company}] triage — 총 {total}건 / DART 계정 후보 {len(accounts)}개(울타리)",
        _financials_line(financials),
        f"담당 계정군: {viewer_account or '(미지정)'}   ※ severity_rank 등 내부값은 표시하지 않음",
        "바구니로 먼저 나누고, 각 바구니 안에서만 심각도순. 아무것도 숨기지 않음(②③은 접힘·접근 가능).",
        "",
    ]
    for b in baskets:
        lines.append(f"━━ {b.label}  ({len(b.items)}건) ━━")
        if not b.items:
            lines.append("   (없음)")
            lines.append("")
            continue
        for pos, j in enumerate(b.items, start=1):
            item = by_index.get(j.source_index)
            title = item.title if item else f"(원문 없음 #{j.source_index})"
            m = j.magnitude
            tags = [j.direction, _magnitude_text(m), _account_tag(j)]
            if j.abstained:
                tags.append("판단 기권")
            lines.append(f"{pos:>2}. [{' · '.join(tags)}] {title}")
            lines.append(f"    이유: {j.one_line_reason}")
            if j.evidence:
                ev = j.evidence[0]
                lines.append(f"    근거: \"{ev.quote}\" ({ev.field})")
            elif j.abstained:
                lines.append(f"    (기권) {j.abstain_reason}")
            if m.ratio_pct is not None:  # 2층: 규모 앵커(분모·근거 금액)
                lines.append(
                    f"    규모근거: {m.denominator} 대비 {_fmt_pct(m.ratio_pct)}"
                    f" (분모 사유: {m.denominator_reason}) | 금액 \"{m.amount_quote}\""
                )
            for link in j.account_links:  # 2층: 계정 연결 가설 + 그 근거
                lines.append(f"    계정가설: {link.account_group} — {link.reason} | 근거 \"{link.quote}\"")
            if j.account_abstained and not j.account_links:
                lines.append(f"    계정: 특정 불가 — {j.account_abstain_reason}")
            lines.append(f"    단계: {j.stage} · 확신도: {j.confidence}")
            lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print('사용법: python -m src.pipeline "회사명" [담당계정군]', file=sys.stderr)
        print('  ※ 수집 개수는 지정하지 않는다 — 최근 1년치를 넓게 자동 수집한다(루프6b).', file=sys.stderr)
        return 2
    company = argv[1]
    viewer_account = argv[2] if len(argv) > 2 else None
    try:
        r = run(company, viewer_account=viewer_account)
    except Exception as exc:  # 키 미설정·rate limit·DART 조회 실패 등은 정직하게 출력하고 멈춘다
        print(f"[중단] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    f = r.funnel
    print(f"[깔때기] 수집 {f['collected']} · 중복정리후 {f['after_dedup']} · 풀판단 {f['judged']}"
          f"  (접힘: 중복 {f['dup_folded']} + 간이무관 {f['irrelevant_folded']})")
    print(render(company, r.items, r.accounts, r.financials, r.baskets, viewer_account))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
