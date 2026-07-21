"""
tests/invariants/test_event_group_folding.py  (검증방 소유 — 루프13 검증)

사건군(event_group) 접기 불변식. 루프13 개발방이 severity_list_view 에 '같은 사건군의 연속
보도를 대표 1건만 위에 두고 나머지는 그 아래로 접는' 도배 완화를 넣었다. 이것이 '병합'이 아니라
'접기'이며(원칙 7 — 각 판단 보존·펼치면 전부 보임), 오병합하지 않고, 정합(증발 0)을 지키는지
독립 검증한다. 표시층 결정론이라 무-API.

못박는 성질:
- 증발 0: (top-level 항목) + (그 아래 접힌 cluster_members) 의 총합 == 입력 visible 수.
- 병합 아님: 접힌 멤버는 각자 완전한 judgment_view(자기 direction·evidence·계정 가설·source_index)를
  그대로 보존한다(요약·삭제·필드소실 없음).
- 대표 = 심각도 순서 첫 등장(새 공식 없음 — 이미 정렬된 입력의 first-seen).
- 오병합 가드: 서로 '다른' event_group 라벨은 절대 한 대표로 묶이지 않는다.
- 빈 라벨("")·단독(그 라벨이 1건뿐)은 접지 않고 제자리(top-level), cluster 표기 없음.
"""
import pytest

from src.api import serialize
from src.judge.schema import Judgment, Magnitude


def _mkj(idx, sev, grp, direction="악재"):
    return Judgment(
        source_index=idx, relevant=True, relevance_reason="r", direction=direction,
        direction_reason="r", stage="확정", intrinsic_risk=True, intrinsic_risk_reason="r",
        magnitude=Magnitude(size="크다", certainty="확정", reason="r"), evidence=[],
        confidence="높음", abstained=False, abstain_reason="", severity_rank=sev,
        one_line_reason=f"j{idx}", subject_role="단독주체", subject_role_reason="근거",
        audit_attention=False, event_group=grp,
    )


class _Item:
    def __init__(self, i): self.title, self.link, self.published = f"T{i}", "", ""


def _fixture():
    # 담합 6건(같은 라벨) + 다른 악재(빈 라벨·1건 라벨 둘) — 이미 심각도순으로 들어온다고 가정.
    judg = [
        _mkj(0, 95, "담합"), _mkj(1, 90, "담합"), _mkj(2, 88, "담합"),
        _mkj(3, 85, "세무조사"), _mkj(4, 80, "담합"), _mkj(5, 78, ""),
        _mkj(6, 70, "담합"), _mkj(7, 60, "자회사청산"), _mkj(8, 55, "담합"),
    ]
    ordered = sorted(judg, key=lambda j: -j.severity_rank)
    by_index = {j.source_index: _Item(j.source_index) for j in judg}
    return judg, serialize.severity_list_view(ordered, by_index)


def _total(view):
    return sum(1 + len(e.get("cluster_members", [])) for e in view)


def test_no_evaporation_top_plus_nested_equals_input():
    """top-level + 접힌 멤버 총합 == 입력 visible(증발 0 — 정합 항등식 불변)."""
    judg, view = _fixture()
    assert _total(view) == len(judg), f"증발: top+nested {_total(view)} != visible {len(judg)}"


def test_fold_is_not_merge_members_keep_own_judgment():
    """접힌 멤버가 각자 완전한 판단 뷰를 보존한다(요약·병합·필드소실 아님)."""
    _, view = _fixture()
    rep = next(e for e in view if e.get("cluster_label") == "담합")
    members = rep["cluster_members"]
    assert [m["source_index"] for m in members] == [1, 2, 4, 6, 8], "멤버 구성/순서가 틀림"
    for m in members:
        # 각 멤버가 자기 판단 필드를 그대로 가진다(대표로 뭉개지지 않음)
        for key in ("source_index", "direction", "evidence", "account_hypotheses", "one_line_reason"):
            assert key in m, f"접힌 멤버가 판단 필드 {key} 를 잃음(요약/병합 의심)"
    assert rep["cluster_count"] == 6, "대표 cluster_count(대표1+멤버5)=6 이 아님"


def test_representative_is_highest_severity_first_seen():
    """대표 = 이미 매겨진 심각도 순서의 첫 등장(새 정렬·공식 없음)."""
    _, view = _fixture()
    rep = next(e for e in view if e.get("cluster_label") == "담합")
    assert rep["source_index"] == 0, "담합 대표가 심각도 최상위(idx0)가 아님"


def test_distinct_labels_never_merged():
    """서로 다른 event_group 라벨은 절대 한 대표로 묶이지 않는다(오병합 가드)."""
    _, view = _fixture()
    labels = [e.get("cluster_label") for e in view if "cluster_label" in e]
    assert labels == ["담합"], f"다른 라벨이 병합되거나 1건 라벨이 군으로 남음: {labels}"


def test_empty_and_singleton_labels_stay_in_place():
    """빈 라벨·단독(라벨 1건뿐)은 접지 않고 top-level 제자리, cluster 표기 없음."""
    _, view = _fixture()
    top = {e["source_index"] for e in view}
    for idx in (3, 5, 7):   # 세무조사(1건)·빈 라벨·자회사청산(1건)
        assert idx in top, f"idx{idx} 가 top-level 에 없음(잘못 접힘)"
    for e in view:
        if e["source_index"] in (3, 5, 7):
            assert "cluster_label" not in e and "cluster_members" not in e, \
                f"idx{e['source_index']} 에 불필요한 cluster 표기가 붙음(잡음)"


def test_ambiguous_empty_label_is_conservative():
    """애매(빈 라벨) 항목은 서로 묶이지 않는다 — 빈 라벨 여러 개여도 각자 top-level(오병합 금지)."""
    judg = [_mkj(0, 90, ""), _mkj(1, 80, ""), _mkj(2, 70, "")]
    by_index = {j.source_index: _Item(j.source_index) for j in judg}
    view = serialize.severity_list_view(judg, by_index)
    assert len(view) == 3 and all("cluster_label" not in e for e in view), \
        "빈 라벨끼리 묶였다(빈 값은 '같은 사건군'이 아니다 — 오병합)"
