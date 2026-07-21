"""
tests/invariants/test_subject_role_demotion.py  (검증방 소유 — 루프10)

불변식(주체성 표시강등: 명백한 C 만 강등하되 무엇도 잃지 않는다):
설계자가 '비교 언급 오염'(타사 기사 속 언급이 그 회사 악재로 뜸)을 줄이려 주체성 3분류
A(단독주체)/B(복수주체)/C(타사언급)를 추가했다(decision-log D29). 판단층은 '값'만 표기하고
표시층이 그 값을 '읽어' C 만 하단으로 내린다. 이 파일은 그 표시강등이 두 위험을 만들지 않음을
독립 검증한다(개발방 unit test/test_subject_role_display.py 는 응시자 자체 테스트라 검증 기준 아님
— 원칙 10 채점자·응시자 분리):

  (A) 놓침(원칙 7): 중요한 기사를 C 로 잘못 내려 시야에서 밀어내는가.
  (B) 하드코딩(원칙 1): 강등이 제목/회사명/키워드 매칭에 의존하는가.

여기서 못박는 안전 성질:
- 강등의 유일한 트리거는 subject_role=='타사언급'(명백한 C) 값이다. A·B·""(미분류)·미판단은
  절대 강등되지 않는다(애매하면 안 내림 — 놓침 방지, 원칙 7).
- 강등은 title/content 에 '구조적으로' 의존할 수 없다: 정렬 함수(rank_for_display·
  demote_peripheral·is_peripheral_mention·classify_and_rank)는 Judgment 만 받고 Judgment 에는
  제목이 없다. 제목은 그 뒤 serialize 단계에서 표시용으로만 붙는다(원칙 1 — 제목 매칭 불가).
  → 이 파일은 그 title-blindness 를 구조·거동 양쪽으로 단언한다.
- severity_rank(순수 LLM 신호) 원값은 강등으로 바뀌지 않는다(원칙 8).
- A 와 B 는 주체성으로 상대순위 차등을 받지 않는다(심각도순 그대로).
- 강등은 삭제·접힘이 아니다(원칙 7): C 악재도 표시분(shown)에 남아 하단·스크롤 접근이고
  folded 로 새지 않는다. 정합(수집=표시+접힘)·증발 0 은 강등 뒤에도 성립한다(치환일 뿐).
- 라우팅 '배정'은 불변이고 '바구니 안 순서'만 C 하단(원칙 7 — ②미배정 숨기지 않음).

경계(정직 — 원칙 4): '실제 분류 정확도'(LLM 이 태영489%를 B, 삼성 브리핑 속 과징금을 C 로
'제대로' 다는지)는 이 코드층 불변식으로 검증되지 않는다 — 그건 LLM 판단이라 라이브 API 가
필요하다. 이 파일은 'LLM 이 role 값을 준다면 표시층이 그 값을 원칙대로 반영하는가'만 못박고,
분류 정확도는 프롬프트(system.txt) 감사 + 라이브로 별도 확인한다.

방법: API 없이 Judgment 값을 직접 구성해(마치 LLM 이 role 을 매긴 것처럼) 표시·정렬·라우팅
      순수 함수와 서버 표시경로(_split_visible·_build_analyze_response)를 태운다.
"""

import pytest

from src.api import serialize
from src.api.server import _build_analyze_response, _split_visible
from src.judge.schema import (
    SUBJECT_MULTI, SUBJECT_PERIPHERAL, SUBJECT_SOLE, AccountLink, Evidence,
    Judgment, Magnitude, is_peripheral_mention, make_missing_judgment,
)
from src.ranking import (
    classify_and_rank, demote_peripheral, rank_by_severity, rank_for_display,
)
from src.sources.base import NewsItem


def _j(si, *, rank=50, role="", direction="악재", account="매출액",
       relevant=True, abstained=False):
    """정상적으로 '판단된' 악재 Judgment(근거·계정 동반). subject_role 만 바꿔 강등 거동을 본다."""
    links = [AccountLink(account_group=account, quote="원문근거", field="제목", reason="왜")] if account else []
    return Judgment(
        source_index=si, relevant=relevant, relevance_reason="r", direction=direction,
        direction_reason="dr", stage="혐의", intrinsic_risk=True, intrinsic_risk_reason="ir",
        magnitude=Magnitude(size="크다", certainty="확정", reason="x"),
        evidence=[Evidence(quote="원문 근거 문장", field="제목")], confidence="높음",
        abstained=abstained, abstain_reason="", severity_rank=rank, one_line_reason="이유",
        account_links=links, account_abstained=not links,
        subject_role=role,
        subject_role_reason=("이 기사의 주체는 다른 회사이며 본문에 이 회사가 한 줄 언급됨"
                             if role == SUBJECT_PERIPHERAL else ""),
    )


def _item(si, title):
    return NewsItem(title=title, snippet=f"스니펫{si}", link=f"http://x/{si}",
                    published="Mon, 14 Jul 2026 09:00:00 +0900", source_index=si)


def _order(judgments):
    return [j.source_index for j in judgments]


# ══════════════════════════════════════════════════════════════════════════════
#  검증 1 — 태스크의 4개 서사 케이스(코드층 실측). LLM 이 매겼을 role 값을 주입한다.
#  핵심 위험: '중요한 기사를 C 로 잘못 내림'. 코드층에서 강등은 오직 role==C 일 때만 →
#  '중요한 것이 내려가는' 유일한 경로는 LLM 오분류뿐임을 이 케이스들이 드러낸다.
# ══════════════════════════════════════════════════════════════════════════════

def test_case_subsidiary_without_parent_name_in_title_stays_up():
    """
    [서사] 지분46% 자회사 관리종목 지정 — 제목에 모회사명 없음. LLM 이 B(복수/단독주체)로 보면
    표시층은 '제목에 회사명 없음'을 이유로 내리지 않는다(원칙 1: 제목 매칭 금지). C 로만 내려간다.
    입력: role=B, 제목에 회사명 없는 자회사 기사, 심각도 중.  기대: 강등 안 됨(상단 유지).
    """
    # 제목에 대상 회사명이 전혀 없다(자회사 기사) — 그래도 role=B 면 안 내려간다.
    sub = _j(0, rank=60, role=SUBJECT_MULTI)     # LLM: 이 회사 실질 사건 → 복수/단독주체
    other = _j(1, rank=40, role=SUBJECT_SOLE)
    out = rank_for_display([sub, other])
    assert _order(out) == [0, 1], "자회사 B(제목에 회사명 없음)가 강등됐다(제목 매칭 오작동)."
    assert not is_peripheral_mention(sub)

    # 서버 표시경로로도: 제목에 회사명 없어도 shown 상단, folded 아님.
    visible, folded = _split_visible([sub, other])
    assert 0 in _order(visible) and 0 not in _order(folded), "자회사 B 가 접힘/누락됐다(놓침)."


def test_case_multi_company_comparison_with_real_figure_stays_up():
    """
    [서사] "건설사 5년 부채비율…이 회사 489%" — 여러 회사 나열이나 대상 회사의 실질 수치가 본문에.
    LLM 이 B(복수주체)로 보면 강등 안 됨(상단 유지). 입력: role=B, 심각도 높음. 기대: 상단.
    """
    multi = _j(0, rank=85, role=SUBJECT_MULTI)
    lo = _j(1, rank=30, role=SUBJECT_SOLE)
    out = rank_for_display([multi, lo])
    assert _order(out) == [0, 1], "복수주체 B(실질 수치 있음)가 강등됐다(중요한 것을 내림)."


def test_case_clear_other_company_briefing_demoted_but_visible():
    """
    [서사] "타사 2분기 영업익…" 주간 브리핑에 이 회사 과징금 한 줄 — LLM 이 C(타사언급)로 보면
    강등되되 삭제·접힘 아님. 입력: role=C, direction=악재, 심각도 최고.
    기대: 표시분(shown) '맨 아래'에 남고 folded 아님. 라벨·설명 노출.
    """
    hi_C = _j(0, rank=95, role=SUBJECT_PERIPHERAL)   # 심각도 최고지만 C
    a = _j(1, rank=70, role=SUBJECT_SOLE)
    b = _j(2, rank=60, role=SUBJECT_MULTI)
    visible, folded = _split_visible([hi_C, a, b])
    assert _order(visible)[-1] == 0, "C(타사언급)가 표시분 맨 아래로 강등되지 않았다."
    assert 0 not in _order(folded), "C 악재가 접힘으로 샜다(원칙 7 — 강등이지 은닉 아님)."
    view = serialize.judgment_view(hi_C, _item(0, "타사 2분기 영업익 발표"))
    assert view["peripheral_mention"] is True and view["subject_note"], "C 라벨·설명이 노출되지 않음."


def test_case_ambiguous_boundary_not_demoted():
    """
    [서사] 애매한 경계 — LLM 이 C 로 단정하지 못하면(프롬프트: 애매하면 B/미분류), role 은 B 또는
    ""(미분류)로 남고 코드는 그런 것을 절대 강등하지 않는다(놓침 방지, 원칙 7).
    입력: role="" (미분류), 심각도 높음.  기대: 강등 안 됨.
    """
    ambiguous = _j(0, rank=88, role="")            # 미분류(애매 → 안 내림)
    low = _j(1, rank=20, role=SUBJECT_SOLE)
    out = rank_for_display([ambiguous, low])
    assert _order(out) == [0, 1], "미분류(애매)가 강등됐다(놓침 위험 — 원칙 7 위반)."
    assert not is_peripheral_mention(ambiguous)


# ══════════════════════════════════════════════════════════════════════════════
#  놓침 방지(원칙 7) — 강등 트리거는 오직 명백한 C
# ══════════════════════════════════════════════════════════════════════════════

def test_only_explicit_C_is_ever_demoted():
    """A·B·""(미분류)·미판단(unjudged)은 강등 대상이 아니다 — 오직 '타사언급'만."""
    a = _j(0, role=SUBJECT_SOLE)
    b = _j(1, role=SUBJECT_MULTI)
    blank = _j(2, role="")
    missing = make_missing_judgment(3)             # 루프9 미판단(role 없음 → 미분류)
    c = _j(4, role=SUBJECT_PERIPHERAL)
    for j in (a, b, blank, missing):
        assert not is_peripheral_mention(j), f"강등되면 안 되는 것이 C로 판정됨: role={j.subject_role!r}"
    assert is_peripheral_mention(c)
    # demote 는 정확히 C 한 건만 뒤로 보낸다(core 의 상대순서는 그대로 보존).
    ordered = [a, b, blank, missing, c]
    assert _order(demote_peripheral(ordered)) == [0, 1, 2, 3, 4]   # c 가 이미 끝 → 순서 유지
    ordered2 = [c, a, b, blank, missing]        # c 가 맨 앞
    # core(a,b,blank,missing) 의 상대순서 0,1,2,3 을 보존하고 c(4)만 맨 뒤로 → [0,1,2,3,4]
    assert _order(demote_peripheral(ordered2)) == [0, 1, 2, 3, 4]


def test_unjudged_is_not_demoted_by_subjecthood():
    """루프9 증발방어로 보존된 미판단이 주체성 강등에 휩쓸리지 않는다(이중 강등 금지)."""
    missing = make_missing_judgment(0)
    assert missing.subject_role == "" and not is_peripheral_mention(missing)
    real_c = _j(1, rank=90, role=SUBJECT_PERIPHERAL)
    # 미판단(rank 0)은 심각도로 이미 바닥이지만, 그건 severity 때문이지 주체성 강등 때문이 아니다.
    out = rank_for_display([real_c, missing])
    # real_c 는 C 라 강등, missing 은 rank 0 이라 심각도로 바닥 — 둘 다 뒤지만 이유가 다름.
    assert set(_order(out)) == {0, 1}, "미판단/ C 중 하나가 증발했다."


# ══════════════════════════════════════════════════════════════════════════════
#  하드코딩 금지(원칙 1) — 강등은 title/content 에 의존할 수 없다
# ══════════════════════════════════════════════════════════════════════════════

def test_demotion_is_structurally_title_blind():
    """
    정렬 함수는 Judgment 만 받는다. Judgment 에는 제목/본문이 없다 → 강등은 제목을 '볼 수 없다'.
    같은 판단 집합의 순서는 제목을 무엇으로 붙이든 동일하다(제목은 정렬 뒤 표시에서만 붙음).
    """
    js = [_j(0, rank=50, role=SUBJECT_PERIPHERAL), _j(1, rank=90, role=SUBJECT_SOLE),
          _j(2, rank=70, role=SUBJECT_MULTI)]
    base = _order(rank_for_display(js))
    # 제목을 극단적으로 바꿔도(회사명 넣기/빼기) 정렬은 Judgment 만 보므로 불변.
    for by_index in (
        {0: "회사명 대문짝 포함 제목", 1: "회사명 전혀 없는 제목", 2: ""},
        {0: "", 1: "회사명 포함", 2: "회사명 포함"},
    ):
        items = [_item(i, by_index[i]) for i in (0, 1, 2)]
        vis, fold = _split_visible(js)   # _split_visible 도 Judgment 만 본다
        assert _order(rank_for_display(js)) == base, "제목을 바꿨더니 정렬이 바뀌었다(제목 의존 — 원칙 1 위반)."
        # 표시 dict 는 제목을 담지만(1층), '순서'는 이미 고정 — 제목이 순서를 바꾸지 않는다.
        _ = [serialize.judgment_view(j, {it.source_index: it for it in items}.get(j.source_index)) for j in vis]


def test_demotion_driven_solely_by_role_value_not_title():
    """
    회사명이 제목에 대문짝만하게 있는 C 는 (그래도) 강등되고, 회사명이 제목에 전혀 없는 B 는
    (그래도) 강등되지 않는다. → 강등의 원인은 오직 role 값, 제목의 회사명 유무가 아니다.
    (제목에 회사명 있으면 살리고 없으면 죽이는 '거절된 하드코딩 설계'의 정반대임을 못박음.)
    """
    c_with_name = _j(0, rank=90, role=SUBJECT_PERIPHERAL)   # (제목에 회사명 있다 치더라도) C → 강등
    b_without_name = _j(1, rank=50, role=SUBJECT_MULTI)     # (제목에 회사명 없다 치더라도) B → 유지
    out = _order(rank_for_display([c_with_name, b_without_name]))
    assert out == [1, 0], "role 이 아니라 제목/회사명으로 강등을 정한 흔적(원칙 1 위반)."
    # role 만 뒤집으면 결과가 뒤집힌다(제목은 그대로) → 원인은 role.
    c_flipped = _j(0, rank=90, role=SUBJECT_MULTI)          # 같은 항목, role 만 B 로
    b_flipped = _j(1, rank=50, role=SUBJECT_PERIPHERAL)     # 같은 항목, role 만 C 로
    out2 = _order(rank_for_display([c_flipped, b_flipped]))
    assert out2 == [0, 1], "role 을 바꿨는데 순서가 안 바뀌었다(강등 원인이 role 이 아님)."


# ══════════════════════════════════════════════════════════════════════════════
#  원칙 8 / 차등 없음 — severity_rank 불변, A·B 무차등
# ══════════════════════════════════════════════════════════════════════════════

def test_severity_rank_untouched_by_demotion():
    """강등은 위치만 바꾼다 — severity_rank 원값(LLM 신호)을 건드리지 않는다(원칙 8)."""
    c = _j(0, rank=93, role=SUBJECT_PERIPHERAL)
    b = _j(1, rank=12, role=SUBJECT_MULTI)
    rank_for_display([c, b]); classify_and_rank([c, b], ["매출액"])
    assert c.severity_rank == 93 and b.severity_rank == 12, "강등이 severity_rank 를 변조했다."


def test_rank_by_severity_ignores_subjecthood():
    """순수 심각도 정렬은 주체성을 섞지 않는다 — C라도 심각도 높으면 위(강등은 별도 층)."""
    hi_C = _j(0, rank=99, role=SUBJECT_PERIPHERAL)
    lo_A = _j(1, rank=1, role=SUBJECT_SOLE)
    assert _order(rank_by_severity([hi_C, lo_A])) == [0, 1], "rank_by_severity 가 주체성에 오염됨."


def test_A_and_B_not_differentiated_by_subjecthood():
    """A 와 B 는 주체성으로 상대순위가 갈리지 않는다 — 심각도순만 따른다(설계상 차등 없음)."""
    lo_A = _j(0, rank=40, role=SUBJECT_SOLE)
    hi_B = _j(1, rank=80, role=SUBJECT_MULTI)
    # 심각도 높은 B 가 위, 낮은 A 가 아래 — role(A vs B)이 순서를 바꾸지 않는다.
    assert _order(rank_for_display([lo_A, hi_B])) == [1, 0]
    # 심각도가 같으면 결정론적 tiebreak(source_index) — 주체성/입력순이 A·B 를 갈라놓지 않는다.
    # 입력 순서를 뒤집어도 결과가 같다 → 주체성으로 인한 차등이 '0'임을 강하게 증명.
    eqA = _j(2, rank=50, role=SUBJECT_SOLE)
    eqB = _j(3, rank=50, role=SUBJECT_MULTI)
    assert _order(rank_for_display([eqA, eqB])) == [2, 3]      # source_index 오름차순(결정론)
    assert _order(rank_for_display([eqB, eqA])) == [2, 3]      # 입력 뒤집어도 동일 → 주체성 무차등


# ══════════════════════════════════════════════════════════════════════════════
#  원칙 7 — 접힘 아님(표시분 하단) · 라우팅 배정 불변 · 정합/증발 0
# ══════════════════════════════════════════════════════════════════════════════

def test_C_routing_assignment_unchanged_only_order():
    """C 도 계정이 걸리면 원래 바구니(①/③)에 그대로 배정되고, 순서만 바구니 안에서 하단."""
    a = _j(0, rank=10, role=SUBJECT_SOLE, account="매출액")
    c = _j(1, rank=95, role=SUBJECT_PERIPHERAL, account="매출액")   # 심각도 높은 C, 같은 계정
    baskets = {b.key: b for b in classify_and_rank([a, c], ["매출액"])}
    mine = _order(baskets["mine"].items)
    assert set(mine) == {0, 1}, "C 가 바구니 배정에서 빠졌다(라우팅 변조 — 숨김)."
    assert mine[-1] == 1, "C 가 바구니 '안에서' 하단으로 가지 않았다."
    # 계정 미배정 C 는 여전히 ②(공용) — 담당자 큐로 새지 않음.
    c_noacct = _j(2, rank=90, role=SUBJECT_PERIPHERAL, account=None)
    b2 = {b.key: b for b in classify_and_rank([a, c, c_noacct], ["매출액"])}
    assert 2 in _order(b2["unassigned"].items), "계정 없는 C 가 ②미배정에 보존되지 않음."
    assert 2 not in _order(b2["mine"].items), "계정 없는 C 가 담당자 큐로 샜다."


def test_demotion_conserves_everything_no_evaporation():
    """
    강등은 '치환'일 뿐 — 어떤 판단도 잃거나 복제하지 않는다. C·미판단·A·B·미분류가 섞여도
    rank_for_display / _split_visible 출력의 source_index 집합은 입력과 정확히 같다(증발 0).
    루프9 증발방어(수집=표시+접힘)가 주체성 강등 뒤에도 성립함을 못박는다.
    """
    js = [_j(0, rank=95, role=SUBJECT_PERIPHERAL),      # C 악재(표시분 하단)
          _j(1, rank=80, role=SUBJECT_SOLE),            # A 악재
          _j(2, rank=70, role=SUBJECT_MULTI),           # B 악재
          _j(3, rank=60, role="", direction="호재", relevant=True),  # 단순 호재 → 접힘
          _j(4, rank=50, role="", relevant=False, account=None),     # 무관 → 접힘
          make_missing_judgment(5)]                     # 미판단 → 접힘(unjudged)
    input_set = set(range(6))
    assert set(_order(rank_for_display(js))) == input_set, "강등이 판단을 잃거나 복제했다."
    assert len(rank_for_display(js)) == len(js), "강등이 건수를 바꿨다."
    visible, folded = _split_visible(js)
    assert set(_order(visible)) | set(_order(folded)) == input_set, "표시+접힘 ≠ 입력(증발)."
    assert set(_order(visible)) & set(_order(folded)) == set(), "표시·접힘 이중 계상."
    # C 악재(0)는 표시분 하단, 접힘 아님.
    assert _order(visible)[-1] == 0 and 0 not in _order(folded)


def test_full_server_response_identity_with_C_present():
    """
    서버 표시경로(_build_analyze_response) 전체에서: 정합(수집=표시+접힘) 성립, C 악재는
    severity_view(표시분) '맨 아래'에 있고 folded_view 에 없다(접힘 아님), 라벨·설명 노출.
    (루프9 response_identity 의 주체성 버전 — 강등 뒤에도 어떤 뉴스도 증발하지 않는다.)
    """
    COLLECTED = 8
    items = [_item(i, f"뉴스{i} 제목") for i in range(COLLECTED)]
    judgments = [
        _j(0, rank=95, role=SUBJECT_PERIPHERAL),        # C 악재 — 표시분 하단
        _j(1, rank=88, role=SUBJECT_SOLE),              # A 악재 — 상단
        _j(2, rank=77, role=SUBJECT_MULTI),             # B 악재
        _j(3, rank=40, role="", direction="호재"),       # 단순 호재 → 접힘
    ]
    survivors = len(judgments)
    prejudge_folded = [(items[i], "중복(대표에 통합)" if i % 2 else "간이 무관") for i in range(4, 8)]
    funnel = {"collected": COLLECTED, "after_dedup": 6, "dup_folded": 2, "irrelevant_folded": 2,
              "truncated": 0, "survivors": survivors, "judged": len(judgments), "unjudged": 0,
              "collection_meta": {}}
    resp = _build_analyze_response("가상사", items, ["매출액"], {"매출액": 1000}, [], judgments,
                                   prejudge_folded=prejudge_folded, funnel=funnel)
    c = resp["counts"]
    sev_idx = [v["source_index"] for v in resp["severity_view"]]
    folded_idx = [v["source_index"] for v in resp["folded_view"]]

    # 정합: 수집 = 표시 + 접힘, 모든 index 접근가능(증발 0)
    assert c["collected"] == COLLECTED == c["shown"] + c["folded"], "정합 깨짐(수집≠표시+접힘)."
    assert sorted(sev_idx + folded_idx) == list(range(COLLECTED)), "일부 뉴스가 표시·접힘 어디에도 없다(증발)."
    # C 악재(0)는 표시분 맨 아래, folded 아님
    assert 0 in sev_idx and sev_idx[-1] == 0, "C 악재가 표시분 하단에 있지 않다."
    assert 0 not in folded_idx, "C 악재가 접힘으로 샜다(은닉 — 원칙 7 위반)."
    # A(1)·B(2)는 심각도순으로 C 위 (주체성이 A·B 차등 안 줌)
    assert sev_idx.index(1) < sev_idx.index(2) < sev_idx.index(0), "A·B·C 표시 순서가 심각도/강등과 다름."
    # C 라벨·설명 노출
    c_view = next(v for v in resp["severity_view"] if v["source_index"] == 0)
    assert c_view["peripheral_mention"] is True and c_view["subject_note"], "C 라벨/설명 누락."
