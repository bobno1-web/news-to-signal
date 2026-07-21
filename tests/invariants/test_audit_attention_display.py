"""
tests/invariants/test_audit_attention_display.py  (검증방 소유 — 루프11)

불변식(감사중요 표시: 판단을 오염하지 않고 배치만 바꾼다):
루프11에서 '방향은 중립이나 감사상 중대한 사건(10조 우발채무·세일앤리스백 등)'이 기본화면서
접히던 공백을, 방향과 독립된 audit_attention(예/아니오) 값으로 보완했다(decision-log D30, C-1).
주체성(루프10)과 같은 설계 — 8번째 심각도 축이 아니고 표시층만 그 값을 읽는다. 검증방은 이
값이 판단(severity_rank·7축)을 오염하지 않고, 방어선(기권·무관)을 넘지 못하며, 없으면 하위호환
됨을 독립 검증한다. 개발방 unit(test_audit_attention_and_routing.py)은 검증 기준이 아니다(원칙 10).

핵심 성질:
- audit_attention=True 는 '관련 있는 비-악재(중립/호재)'만 기본화면에 올린다(공백 보완).
- 방어선 우선(원칙 3·4): 기권(abstained)·무관(not relevant)은 audit=True 여도 노출 안 함
  (근거 없는데 '중요'로 노출하면 원칙 3 위반). default_visible 이 audit 보다 먼저 그 둘을 막는다.
- 판단 불변(원칙 8): severity_rank 원값·심각도 정렬은 audit 로 바뀌지 않는다(표시 배치만).
- 하위호환: audit 필드가 없거나 False 면 기존 동작(중립 접힘) 그대로.
- 표시층은 값을 '읽기만' 한다 — audit 를 재계산하지 않는다(파싱만, 코드 규칙 없음).

방법: 실제 API 없이 Judgment 값을 직접 구성해 표시 판정(default_visible)·정렬(rank_by_severity)·
      직렬화(judgment_view)를 태운다.
"""

from dataclasses import asdict

from src.api import serialize
from src.judge.schema import (
    AccountLink, Evidence, Judgment, Magnitude, parse_batch,
)
from src.ranking import rank_by_severity


def _j(*, direction="중립", relevant=True, abstained=False, audit=False,
       stage="해당없음", rank=50, si=0, has_evidence=True):
    ev = [Evidence(quote="원문 근거 문장", field="제목")] if has_evidence else []
    return Judgment(
        source_index=si, relevant=relevant, relevance_reason="r", direction=direction,
        direction_reason="dr", stage=stage, intrinsic_risk=False, intrinsic_risk_reason="",
        magnitude=Magnitude("미상", "불명", "x"), evidence=ev, confidence="보통",
        abstained=abstained, abstain_reason="", severity_rank=rank, one_line_reason="이유",
        account_links=[], account_abstained=True,
        audit_attention=audit,
        audit_attention_reason=("감사인 후속확인 필요 이유" if audit else ""),
    )


# ── 노출/과다노출 방지 ────────────────────────────────────────────────────────

def test_audit_true_exposes_relevant_neutral():
    """관련 있는 중립이 audit=True 면 기본화면에 노출(중대 감사포인트 공백 보완)."""
    assert serialize.default_visible(_j(direction="중립", audit=True)) is True


def test_audit_false_neutral_stays_folded():
    """audit=False 인 단순 중립은 여전히 접힘(과다노출 방지 — 원칙 7 노이즈 균형)."""
    assert serialize.default_visible(_j(direction="중립", audit=False)) is False


# ── 방어선 우선(원칙 3·4): 기권·무관은 audit 를 이긴다 ──────────────────────────

def test_abstain_beats_audit():
    """기권(근거 부족 강등)은 audit=True 여도 노출 안 함 — 근거 없는데 '중요' 노출 금지(원칙 3)."""
    assert serialize.default_visible(_j(direction="중립", audit=True, abstained=True)) is False


def test_irrelevant_beats_audit():
    """재무·감사 무관은 audit=True 여도 노출 안 함(방어선 우선 — 원칙 4)."""
    assert serialize.default_visible(_j(direction="중립", audit=True, relevant=False)) is False


def test_forced_abstain_from_missing_evidence_beats_audit():
    """
    근거 없는 비-기권 실질주장은 parse_batch 가 강제기권으로 강등한다(원칙 3). 그렇게 강등된
    항목은 audit=True 라도 노출되지 않아야 한다(강등 방어선이 audit 보다 우선).
    """
    row = {
        "source_index": 0, "relevant": True, "relevance_reason": "r",
        "direction": "악재", "direction_reason": "d", "stage": "혐의",
        "intrinsic_risk": True, "intrinsic_risk_reason": "i",
        "magnitude": {"size": "미상", "certainty": "불명", "reason": "x"},
        "evidence": [],                       # 근거 없음 → 강제기권 대상
        "confidence": "보통", "abstained": False, "abstain_reason": "",
        "severity_rank": 90, "one_line_reason": "",
        "audit_attention": True, "audit_attention_reason": "확인 필요",
        "account_links": [], "account_abstained": True, "account_abstain_reason": "",
    }
    j = parse_batch({"judgments": [row]})[0]
    assert j.abstained is True, "근거 없는 실질주장이 강제기권으로 강등되지 않았다(원칙 3 방어선)."
    assert serialize.default_visible(j) is False, "강제기권인데 audit=True 로 노출됐다(원칙 3 위반)."


# ── 원칙 8: 판단(severity_rank·정렬) 불변 ───────────────────────────────────────

def test_audit_does_not_change_severity_rank_value():
    """audit 는 severity_rank 원값을 바꾸지 않는다(표시 배치만, 원칙 8)."""
    j_on = _j(audit=True, rank=42)
    j_off = _j(audit=False, rank=42)
    serialize.default_visible(j_on); serialize.default_visible(j_off)
    assert j_on.severity_rank == 42 and j_off.severity_rank == 42


def test_audit_does_not_affect_severity_ordering():
    """심각도 정렬은 audit 를 섞지 않는다 — audit=True 라도 심각도 낮으면 아래."""
    hi_no_audit = _j(rank=90, audit=False, si=0)
    lo_audit = _j(rank=10, audit=True, si=1)
    order = [j.source_index for j in rank_by_severity([lo_audit, hi_no_audit])]
    assert order == [0, 1], "audit 가 심각도 정렬을 끌어올렸다(원칙 8 위반)."


def test_bad_news_unaffected_by_audit_value():
    """악재는 audit 값과 무관하게 노출(기존 규칙 불변 — audit 는 중립/호재 공백만 보완)."""
    assert serialize.default_visible(_j(direction="악재", audit=True)) is True
    assert serialize.default_visible(_j(direction="악재", audit=False)) is True


# ── 표시층 read-only + 하위호환 ────────────────────────────────────────────────

def test_judgment_view_exposes_audit_readonly():
    """judgment_view 는 audit 값·사유를 노출만 한다(재계산 없음)."""
    v = serialize.judgment_view(_j(direction="중립", audit=True), None)
    assert v["audit_attention"] is True and v["audit_note"], "audit 노출 실패."
    v2 = serialize.judgment_view(_j(direction="중립", audit=False), None)
    assert v2["audit_attention"] is False and v2["audit_note"] == ""


def test_missing_audit_field_is_backward_compatible():
    """구 dict/구덤프(audit 필드 없음) → False 로 파싱 → 기존 중립 접힘 동작 유지."""
    row = {
        "source_index": 0, "relevant": True, "relevance_reason": "r",
        "direction": "중립", "direction_reason": "", "stage": "해당없음",
        "intrinsic_risk": False, "intrinsic_risk_reason": "",
        "magnitude": {"size": "미상", "certainty": "불명", "reason": "x"},
        "evidence": [{"quote": "근거", "field": "제목"}], "confidence": "보통",
        "abstained": False, "abstain_reason": "", "severity_rank": 50, "one_line_reason": "",
        "account_links": [], "account_abstained": True, "account_abstain_reason": "",
    }  # audit_attention 없음
    j = parse_batch({"judgments": [row]})[0]
    assert j.audit_attention is False, "audit 필드 없는 구 dict 가 True 로 파싱됐다."
    assert serialize.default_visible(j) is False, "구덤프 중립이 노출됐다(하위호환 깨짐)."
    # 덤프 왕복도 False 유지
    d = asdict(_j(direction="중립", audit=True))
    d.pop("audit_attention"); d.pop("audit_attention_reason")
    restored = serialize._judgment_from_dict(d)
    assert restored.audit_attention is False, "루프11 이전 덤프가 audit=True 로 복원됐다."


def test_unjudged_missing_not_exposed_by_audit():
    """루프9 미판단(강제기권·unjudged)은 audit 로도 노출되지 않는다(기권 방어선 우선)."""
    from src.judge.schema import make_missing_judgment
    m = make_missing_judgment(0)
    # 미판단은 audit 필드가 없어 False 지만, 설령 누가 True 를 넣어도 abstained=True 라 접힌다.
    m.audit_attention = True
    assert serialize.default_visible(m) is False, "미판단이 audit=True 로 노출됐다(기권 방어선 우회)."
