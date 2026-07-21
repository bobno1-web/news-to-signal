"""
루프10 개발방 단위 테스트(주체성 표시 강등 — tests/unit, 원칙 10: 개발방 자체 테스트는 여기).

관점: '이 기사가 이 회사를 다루고 있는가'를 LLM이 A(단독주체)·B(복수주체)·C(타사언급)로
표기하면, 표시층이 그 값을 '읽어' C만 하단으로 내린다(삭제·접힘이 아니라 위치만).

여기서 못박는 개발방 시공(검증방 불변식과는 별개):
- is_peripheral_mention: 명백한 C에서만 True. ""(미분류)·A·B 는 False(애매하면 안 내림).
- demote_peripheral / rank_for_display: C만 뒤로, A/B 상대순서 불변, severity_rank 불변.
- classify_and_rank: 바구니 '배정'은 불변, 바구니 '안에서'만 C 하단.
- default_visible: 주체성으로 바뀌지 않는다(C 악재도 표시분에 남되 순서만 내려감 — 원칙 7).
- judgment_view: peripheral_mention·subject_note 노출. 구덤프(주체성 없음) 하위호환.
- 판단 로직 불변: parse_batch 가 주체성을 실어 나르되, 없어도(구 dict) 미분류로 파싱.
"""
from dataclasses import asdict

from src.api import serialize
from src.api.server import _split_visible
from src.judge.schema import (
    SUBJECT_MULTI, SUBJECT_PERIPHERAL, SUBJECT_SOLE, AccountLink, Evidence,
    Judgment, Magnitude, is_peripheral_mention, parse_batch,
)
from src.ranking import classify_and_rank, demote_peripheral, rank_by_severity, rank_for_display
from src.sources.base import NewsItem


def _j(si, *, rank=50, role="", direction="악재", account="매출액", abstained=False):
    """테스트용 Judgment. subject_role 만 바꿔 강등 거동을 본다(나머지는 정상 악재)."""
    links = [AccountLink(account_group=account, quote="q", field="제목", reason="왜")] if account else []
    return Judgment(
        source_index=si, relevant=True, relevance_reason="r", direction=direction,
        direction_reason="dr", stage="혐의", intrinsic_risk=False, intrinsic_risk_reason="",
        magnitude=Magnitude(size="미상", certainty="불명", reason="x"),
        evidence=[Evidence(quote="근거문장", field="제목")], confidence="보통",
        abstained=abstained, abstain_reason="", severity_rank=rank, one_line_reason="이유",
        account_links=links, account_abstained=not links,
        subject_role=role, subject_role_reason=("이 기사의 주체는 다른 회사" if role == SUBJECT_PERIPHERAL else ""),
    )


def _item(si, title="제목"):
    return NewsItem(title=title, snippet="s", link=f"http://x/{si}", published="2026-07-18", source_index=si)


# ── 판별 술어 ────────────────────────────────────────────────────────────────

def test_is_peripheral_only_for_explicit_C():
    assert is_peripheral_mention(_j(0, role=SUBJECT_PERIPHERAL))       # C → True
    assert not is_peripheral_mention(_j(1, role=SUBJECT_SOLE))         # A → False
    assert not is_peripheral_mention(_j(2, role=SUBJECT_MULTI))        # B → False
    assert not is_peripheral_mention(_j(3, role=""))                   # 미분류 → False(애매하면 안 내림)


# ── 강등 정렬 ────────────────────────────────────────────────────────────────

def test_demote_pushes_C_to_bottom_keeping_AB_order():
    """C만 뒤로. A/B 상대순서는 그대로(주체성으로 순위 재계산 안 함)."""
    ordered = [_j(0, role=SUBJECT_SOLE), _j(1, role=SUBJECT_PERIPHERAL),
               _j(2, role=SUBJECT_MULTI), _j(3, role=SUBJECT_PERIPHERAL)]
    out = [j.source_index for j in demote_peripheral(ordered)]
    assert out == [0, 2, 1, 3]      # A,B 앞(원순서 0,2) · C 뒤(원순서 1,3)


def test_rank_for_display_sinks_high_severity_C_below_low_severity_AB():
    """심각도 높은 C가 심각도 낮은 A/B보다 아래로(크게 강등). severity_rank 원값은 불변."""
    hi_C = _j(0, rank=99, role=SUBJECT_PERIPHERAL)
    lo_B = _j(1, rank=1, role=SUBJECT_MULTI)
    out = rank_for_display([hi_C, lo_B])
    assert [j.source_index for j in out] == [1, 0]     # B(낮은 심각도) 위, C(높은 심각도) 아래
    assert hi_C.severity_rank == 99 and lo_B.severity_rank == 1   # 원값 안 건드림(원칙 8)


def test_rank_by_severity_stays_pure():
    """rank_by_severity 는 주체성을 섞지 않는다(순수 심각도) — C라도 심각도 높으면 위."""
    hi_C = _j(0, rank=99, role=SUBJECT_PERIPHERAL)
    lo_B = _j(1, rank=1, role=SUBJECT_MULTI)
    assert [j.source_index for j in rank_by_severity([hi_C, lo_B])] == [0, 1]  # 심각도순 그대로


# ── 바구니: 배정 불변, 순서만 강등 ────────────────────────────────────────────

def test_classify_demotes_C_within_basket_but_not_routing():
    """C도 계정이 걸리면 원래 바구니에 담긴다(배정 불변). 순서만 바구니 안에서 하단."""
    a = _j(0, rank=10, role=SUBJECT_SOLE, account="매출액")
    c = _j(1, rank=90, role=SUBJECT_PERIPHERAL, account="매출액")   # 심각도 높은 C
    baskets = {b.key: b for b in classify_and_rank([a, c], ["매출액"])}
    mine = baskets["mine"].items
    assert [j.source_index for j in mine] == [0, 1]   # C(1)는 배정은 ①에 남고 순서만 뒤로
    assert is_peripheral_mention(mine[-1])            # 맨 아래가 C


# ── default_visible 은 주체성으로 바뀌지 않는다(원칙 7: 강등이지 접힘 아님) ──────

def test_C_bad_stays_visible_just_demoted():
    """C 악재는 접히지 않고 표시분에 남되(원칙 7), 표시분 하단으로 간다."""
    ab = [_j(0, rank=80, role=SUBJECT_SOLE), _j(1, rank=70, role=SUBJECT_MULTI)]
    c = _j(2, rank=95, role=SUBJECT_PERIPHERAL)     # 심각도 최고지만 C
    visible, folded = _split_visible(ab + [c])
    vidx = [j.source_index for j in visible]
    assert 2 in vidx                                 # C 악재는 삭제·접힘 아님(표시분에 남음)
    assert vidx[-1] == 2                             # 그러나 표시분 '맨 아래'
    assert 2 not in [j.source_index for j in folded] # 접힘으로 새지 않음


# ── 화면 변환: 라벨·설명 노출 + 하위호환 ──────────────────────────────────────

def test_judgment_view_exposes_peripheral_and_note():
    view = serialize.judgment_view(_j(0, role=SUBJECT_PERIPHERAL), _item(0))
    assert view["peripheral_mention"] is True
    assert view["subject_note"]                       # 설명 문장 존재
    ab = serialize.judgment_view(_j(1, role=SUBJECT_MULTI), _item(1))
    assert ab["peripheral_mention"] is False


def test_old_dump_without_subject_role_is_not_demoted():
    """루프10 이전 덤프(주체성 필드 없음) → 미분류로 복원 → 강등 안 함(회귀 없음)."""
    d = asdict(_j(0, role=SUBJECT_SOLE))
    d.pop("subject_role"); d.pop("subject_role_reason")     # 구덤프 재현
    restored = serialize._judgment_from_dict(d)
    assert restored.subject_role == ""                      # 미분류
    assert not is_peripheral_mention(restored)              # 강등 대상 아님


def test_parse_batch_carries_subject_role():
    row = {
        "source_index": 0, "relevant": True, "relevance_reason": "r",
        "direction": "악재", "direction_reason": "d", "stage": "혐의",
        "intrinsic_risk": False, "intrinsic_risk_reason": "",
        "magnitude": {"size": "미상", "certainty": "불명", "reason": "x"},
        "evidence": [{"quote": "근거", "field": "제목"}], "confidence": "보통",
        "abstained": False, "abstain_reason": "", "severity_rank": 50,
        "one_line_reason": "이유", "subject_role": "타사언급",
        "subject_role_reason": "이 기사의 주체는 다른 회사",
        "account_links": [], "account_abstained": True, "account_abstain_reason": "",
    }
    out = parse_batch({"judgments": [row]})
    assert out[0].subject_role == SUBJECT_PERIPHERAL
    assert is_peripheral_mention(out[0])
    # 주체성 필드가 없는 구 dict 도 파싱된다(미분류)
    row2 = dict(row); row2.pop("subject_role"); row2.pop("subject_role_reason")
    out2 = parse_batch({"judgments": [row2]})
    assert out2[0].subject_role == "" and not is_peripheral_mention(out2[0])
