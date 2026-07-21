"""
tests/unit/test_routing_multi.py  (개발방 소유 — 루프4 신규 기능의 단위 테스트)

원칙 10 경계: 검증방 소유 라우팅 불변식은 tests/invariants/test_routing.py 에 있고
이 파일은 건드리지 않는다. 여기서는 개발방이 루프4에서 추가한 '다중 계정 선택'
(화면 담당자가 여러 계정을 고르는 표시 렌즈)이 기존 단일-문자열 경로와 호환되며,
어떤 뉴스도 삭제하지 않고, 계정 선택이 '담기는 바구니만' 바꾸는지 확인한다.

모두 비-API 결정론적 단위 테스트다.
"""

from src.judge.schema import AccountLink, Judgment, Magnitude
from src.ranking import classify_and_rank


def _j(idx: int, rank: int, account: str | None) -> Judgment:
    links = [AccountLink(account_group=account, quote="근거", field="스니펫", reason="가설")] if account else []
    return Judgment(
        source_index=idx, relevant=True, relevance_reason="", direction="악재",
        direction_reason="", stage="해당없음", intrinsic_risk=False, intrinsic_risk_reason="",
        magnitude=Magnitude("미상", "불명", ""), evidence=[], confidence="보통",
        abstained=False, abstain_reason="", severity_rank=rank, one_line_reason="",
        account_links=links, account_abstained=(account is None),
        account_abstain_reason="특정 불가" if account is None else "",
    )


def _keys(baskets, key):
    return next(b for b in baskets if b.key == key)


def _sample():
    return [
        _j(0, 90, "매출채권및기타채권"),   # A
        _j(1, 80, "재고자산"),             # B
        _j(2, 70, "장기차입금"),           # C
        _j(3, 60, None),                   # 미배정
    ]


def test_multi_select_unions_into_mine():
    """여러 계정을 고르면, 그중 '어느 하나'에 걸린 뉴스가 모두 ① 내 계정으로 온다."""
    baskets = classify_and_rank(_sample(), viewer_account=["매출채권", "재고자산"])
    mine = {j.source_index for j in _keys(baskets, "mine").items}
    others = {j.source_index for j in _keys(baskets, "others").items}
    un = {j.source_index for j in _keys(baskets, "unassigned").items}
    assert mine == {0, 1}, f"다중 선택이 합집합으로 ①에 오지 않았다: {mine}"
    assert others == {2}, f"고르지 않은 계정 뉴스가 ③에 있지 않다: {others}"
    assert un == {3}, "미배정 뉴스가 ② 공용 큐에 남지 않았다(숨기면 안 됨)."


def test_single_string_and_singleton_list_agree():
    """하위호환: 문자열 하나 == 원소 하나짜리 리스트 (같은 결과)."""
    js = _sample()
    a = classify_and_rank(js, viewer_account="재고자산")
    b = classify_and_rank(js, viewer_account=["재고자산"])
    assert [j.source_index for j in _keys(a, "mine").items] == \
           [j.source_index for j in _keys(b, "mine").items] == [1]


def test_selection_only_changes_bucket_not_judgment():
    """계정 선택은 표시 렌즈다: 같은 뉴스의 판단(심각도 순서·방향·계정가설)은 불변,
    담기는 바구니만 달라진다."""
    js = _sample()
    b1 = classify_and_rank(js, viewer_account=["매출채권"])
    b2 = classify_and_rank(js, viewer_account=["재고자산"])
    # 뉴스 0 은 선택에 따라 ①↔③ 바구니만 이동하되, 그 판단 객체 자체는 그대로다.
    j0_in_b1 = next(j for bk in b1 for j in bk.items if j.source_index == 0)
    j0_in_b2 = next(j for bk in b2 for j in bk.items if j.source_index == 0)
    assert j0_in_b1 is j0_in_b2, "라우팅이 판단 객체를 복제/변형했다(렌즈가 아니라 개입)."
    assert j0_in_b1.direction == "악재" and j0_in_b1.severity_rank == 90


def test_empty_list_is_unassigned_viewer():
    """빈 리스트 == 담당 미지정(None): 배정된 뉴스는 ③으로, 총건수 보존."""
    js = _sample()
    for viewer in ([], None):
        baskets = classify_and_rank(js, viewer_account=viewer)
        total = sum(len(b.items) for b in baskets)
        assert len(_keys(baskets, "mine").items) == 0
        assert total == len(js), f"viewer={viewer!r}: 총건수 {total} ≠ {len(js)} (삭제 발생)."
