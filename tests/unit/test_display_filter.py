"""
tests/unit/test_display_filter.py  (개발방 소유 — 루프5 표시 규칙 단위 테스트)

원칙 10 경계: 검증방 불변식(tests/invariants/)은 건드리지 않는다. 여기서는 개발방이
루프5에서 추가한 '기본 화면 표시 규칙'(악재+위험해소만 노출, 나머지는 접힘)이
새 키워드표가 아니라 '기존 판단값(direction·relevant·stage·abstained)'만 읽는지,
그리고 접힌 것이 '삭제'가 아니라 '분류'인지 확인한다.

핵심 검증: '위험 해소 vs 단순 호재'는 stage=='무혐의/취하'(기존 판단 enum 값)로 갈린다 —
뉴스 본문 단어 매칭이 아니다. 모두 비-API 결정론적 단위 테스트.
"""

from src.api.serialize import default_visible, fold_reason, is_resolution
from src.judge.schema import Judgment, Magnitude


def _j(*, relevant=True, direction="악재", stage="해당없음", abstained=False) -> Judgment:
    return Judgment(
        source_index=0, relevant=relevant, relevance_reason="", direction=direction,
        direction_reason="", stage=stage, intrinsic_risk=False, intrinsic_risk_reason="",
        magnitude=Magnitude("미상", "불명", ""), evidence=[], confidence="보통",
        abstained=abstained, abstain_reason="", severity_rank=50, one_line_reason="",
    )


def test_bad_news_is_visible():
    """재무·감사 악재는 기본 화면에 노출된다."""
    assert default_visible(_j(relevant=True, direction="악재")) is True


def test_risk_resolution_is_visible_via_stage():
    """위험 해소(무혐의/취하)는 노출된다 — 방향이 호재/중립이어도 stage 로 식별(기존 판단값)."""
    assert is_resolution(_j(direction="호재", stage="무혐의/취하")) is True
    assert default_visible(_j(relevant=True, direction="호재", stage="무혐의/취하")) is True
    assert default_visible(_j(relevant=True, direction="중립", stage="무혐의/취하")) is True


def test_plain_good_news_is_folded():
    """단순 호재(관련O·호재·비해소)는 접힌다. '위험 해소'와의 경계는 stage 뿐이다."""
    plain = _j(relevant=True, direction="호재", stage="해당없음")   # 수주·실적 등
    assert default_visible(plain) is False
    assert fold_reason(plain) == "단순 호재(위험 해소 아님)"


def test_irrelevant_is_folded():
    """재무·감사 무관(relevant=False)은 접힌다."""
    noise = _j(relevant=False, direction="중립", stage="해당없음")
    assert default_visible(noise) is False
    assert fold_reason(noise) == "재무·감사 무관"


def test_relevant_neutral_is_folded():
    """관련은 있으나 위험 신호가 아닌 중립은 접힌다(악재도 해소도 아님)."""
    neu = _j(relevant=True, direction="중립", stage="해당없음")
    assert default_visible(neu) is False
    assert fold_reason(neu) == "중립(위험 신호 아님)"


def test_abstained_is_folded_but_reasoned():
    """근거 부족으로 기권된 판단은 접되(원칙 3), 이유를 달아 접근 가능하게(원칙 7)."""
    ab = _j(relevant=True, direction="악재", abstained=True)   # 원래 악재였어도 근거 없으면 강등
    assert default_visible(ab) is False
    assert fold_reason(ab) == "판단 기권(근거 부족)"


def test_filter_reads_only_existing_values_not_text():
    """표시 규칙은 판단값만 읽는다 — 같은 본문이라도 판단값이 다르면 결과가 갈린다
    (즉 뉴스 텍스트가 아니라 direction/stage 를 본다). 하드코딩 아님의 증거."""
    resolved = _j(relevant=True, direction="호재", stage="무혐의/취하")
    plain = _j(relevant=True, direction="호재", stage="해당없음")
    assert default_visible(resolved) != default_visible(plain), \
        "위험해소/단순호재를 stage(판단값)로 가르지 못했다."
