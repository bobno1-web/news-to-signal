"""
루프11 개발방 단위 테스트(감사중요 중립 노출 + 선택 전 라우팅 — tests/unit, 원칙 10).

C-1 감사중요: 방향과 독립된 audit_attention=True 면 '중립'이어도 기본화면에 노출한다.
  표시층이 판단값을 '읽기만' 한다(재계산 없음) — default_visible 이 direction 읽는 것과 동일.
  과다 노출 방지: audit_attention=False 인 단순 중립은 여전히 접힘.
C-4a 라우팅: 담당 계정 미선택이면 '남의 계정(③)'이 성립 안 하므로 계정 붙은 뉴스도 ② 공용에.

검증방 소유 불변식(tests/invariants/)은 별개다 — 여기선 개발방이 자기 시공을 못박는다.
"""
from dataclasses import asdict

from src.api import serialize
from src.judge.schema import (
    AccountLink, Evidence, Judgment, Magnitude, parse_batch,
)
from src.ranking import classify_and_rank


def _j(*, direction="중립", relevant=True, abstained=False, audit=False, stage="해당없음",
       rank=50, account=None):
    links = [AccountLink(account_group=account, quote="q", field="제목", reason="왜")] if account else []
    return Judgment(
        source_index=0, relevant=relevant, relevance_reason="", direction=direction,
        direction_reason="", stage=stage, intrinsic_risk=False, intrinsic_risk_reason="",
        magnitude=Magnitude("미상", "불명", ""), evidence=[Evidence(quote="근거", field="제목")],
        confidence="보통", abstained=abstained, abstain_reason="", severity_rank=rank,
        one_line_reason="", account_links=links, account_abstained=not links,
        audit_attention=audit,
        audit_attention_reason=("감사 확인 필요 이유" if audit else ""),
    )


# ── C-1: 감사중요 노출/과다노출 방지 ─────────────────────────────────────────

def test_audit_neutral_becomes_visible():
    """방향 중립이라도 audit_attention=True 면 노출된다(중대한 감사 포인트 공백 보완)."""
    assert serialize.default_visible(_j(direction="중립", audit=True)) is True


def test_plain_neutral_without_audit_still_folded():
    """audit_attention=False 인 단순 중립은 여전히 접힘(과다 노출 방지)."""
    assert serialize.default_visible(_j(direction="중립", audit=False)) is False


def test_audit_does_not_override_abstain_or_irrelevant():
    """감사중요라도 기권(근거 부족)·무관은 노출 안 함(방어선 우선 — 원칙 3·4)."""
    assert serialize.default_visible(_j(direction="중립", audit=True, abstained=True)) is False
    assert serialize.default_visible(_j(direction="중립", audit=True, relevant=False)) is False


def test_bad_news_unaffected_by_audit():
    """악재는 audit 값과 무관하게 노출(기존 규칙 불변)."""
    assert serialize.default_visible(_j(direction="악재", audit=False)) is True


def test_judgment_view_exposes_audit():
    view = serialize.judgment_view(_j(direction="중립", audit=True), None)
    assert view["audit_attention"] is True and view["audit_note"]
    v2 = serialize.judgment_view(_j(direction="중립", audit=False), None)
    assert v2["audit_attention"] is False


# ── 스키마 왕복 + 하위호환 ────────────────────────────────────────────────────

def test_parse_batch_and_backcompat():
    row = {
        "source_index": 0, "relevant": True, "relevance_reason": "r",
        "direction": "중립", "direction_reason": "", "stage": "해당없음",
        "intrinsic_risk": False, "intrinsic_risk_reason": "",
        "magnitude": {"size": "미상", "certainty": "불명", "reason": "x"},
        "evidence": [], "confidence": "보통", "abstained": False, "abstain_reason": "",
        "severity_rank": 50, "one_line_reason": "", "audit_attention": True,
        "audit_attention_reason": "확인 필요", "account_links": [],
        "account_abstained": True, "account_abstain_reason": "",
    }
    out = parse_batch({"judgments": [row]})
    assert out[0].audit_attention is True and out[0].audit_attention_reason == "확인 필요"
    row2 = dict(row); row2.pop("audit_attention"); row2.pop("audit_attention_reason")
    out2 = parse_batch({"judgments": [row2]})    # 구 dict → 기본 False
    assert out2[0].audit_attention is False


def test_old_dump_without_audit_loads_false():
    d = asdict(_j(direction="중립", audit=True))
    d.pop("audit_attention"); d.pop("audit_attention_reason")
    restored = serialize._judgment_from_dict(d)
    assert restored.audit_attention is False   # 루프11 이전 덤프 → 노출 강제 안 함


# ── C-4a: 담당 미선택 라우팅 ─────────────────────────────────────────────────

def test_no_viewer_routes_linked_to_common_not_others():
    """담당 미선택: 계정 붙은 뉴스도 ③(남의 계정) 아니라 ② 공용에. ③은 빈다."""
    js = [_j(account="매출액", rank=r) for r in (90, 80)]
    for i, j in enumerate(js):
        j.source_index = i
    baskets = {b.key: b for b in classify_and_rank(js, viewer_account=None)}
    assert baskets["others"].items == []          # 선택 전엔 '남의 계정' 없음
    assert len(baskets["unassigned"].items) == 2  # 계정 붙었어도 선택 전엔 공용
    assert "선택 전" in baskets["others"].label


def test_viewer_selected_still_routes_others():
    """담당 선택 시엔 기존대로: 내 계정 아닌 계정 붙은 뉴스는 ③으로(회귀 없음)."""
    a = _j(account="매출액"); a.source_index = 0
    b = _j(account="재고자산"); b.source_index = 1
    baskets = {x.key: x for x in classify_and_rank([a, b], viewer_account="매출액")}
    assert [j.source_index for j in baskets["mine"].items] == [0]
    assert [j.source_index for j in baskets["others"].items] == [1]
