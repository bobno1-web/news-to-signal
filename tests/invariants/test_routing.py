"""
tests/invariants/test_routing.py  (검증방 소유 — 루프2)

검증 3: 라우팅 원칙(routing-rules.md).
- ② '미배정' 바구니가 숨겨지지 않고 항상 존재/접근 가능한가(원칙 7).
- 심각도 정렬이 바구니를 '가로지르지' 않는가(남의 계정 심각 뉴스가 내 계정 위로
  올라오면 라우팅이 깨진다).
- 계정 '하드 필터'(특정 계정만 남기고 나머지 삭제)가 없는가 — 총건수가 보존되는가.

무-API 결정론적 단위 테스트. Judgment 를 직접 구성해 classify_and_rank 를 시험한다.
"""

from src.judge.schema import AccountLink, Judgment
from src.ranking import classify_and_rank


def _j(idx: int, rank: int, *, account: str | None = None,
       account_abstained: bool = False) -> Judgment:
    """테스트용 Judgment. account 가 주어지면 그 계정군에 연결(근거 동반)."""
    links = (
        [AccountLink(account_group=account, quote="근거", field="스니펫", reason="가설")]
        if account else []
    )
    return Judgment(
        source_index=idx, relevant=True, relevance_reason="", direction="악재",
        direction_reason="", stage="해당없음", intrinsic_risk=False, intrinsic_risk_reason="",
        magnitude=__import__("src.judge.schema", fromlist=["Magnitude"]).Magnitude("미상", "불명", ""),
        evidence=[], confidence="보통", abstained=False, abstain_reason="",
        severity_rank=rank, one_line_reason="",
        account_links=links, account_abstained=account_abstained,
        account_abstain_reason="특정 불가" if account_abstained else "",
    )


def _basket(baskets, key):
    return next(b for b in baskets if b.key == key)


def test_unassigned_basket_always_present_and_accessible():
    """계정을 못 붙인 뉴스는 ② 공용 큐에 남고, 그 바구니는 항상 결과에 존재한다(숨기지 않음)."""
    js = [
        _j(0, 90, account="매출채권"),        # ① 담당(viewer=매출채권)
        _j(1, 80, account_abstained=True),    # ② 미배정(계정 특정 불가)
        _j(2, 70, account="재고자산"),        # ③ 타 계정
    ]
    baskets = classify_and_rank(js, viewer_account="매출채권")
    keys = [b.key for b in baskets]
    unassigned = _basket(baskets, "unassigned")
    print("\n[routing] baskets=%s / 미배정 건수=%d" % (keys, len(unassigned.items)))

    assert "unassigned" in keys, "미배정 바구니가 결과에서 사라졌다(원칙 7 위반)."
    assert len(unassigned.items) == 1 and unassigned.items[0].source_index == 1, (
        "계정 특정 불가 뉴스가 공용 큐(②)에 남지 않았다."
    )


def test_severity_sort_does_not_cross_baskets():
    """
    바구니를 가로지르는 정렬 금지: ③(타 계정)의 초고심각(99) 뉴스가 ①(내 계정)의
    저심각(10) 뉴스보다 '위 바구니'로 올라오면 안 된다. 정렬은 바구니 '안에서만'.
    """
    js = [
        _j(0, 10, account="매출채권"),   # ① 내 계정, 낮은 심각도
        _j(1, 99, account="재고자산"),   # ③ 타 계정, 매우 높은 심각도
    ]
    baskets = classify_and_rank(js, viewer_account="매출채권")
    mine, others = _basket(baskets, "mine"), _basket(baskets, "others")
    print("\n[cross] ① idx=%s ranks=%s / ③ idx=%s ranks=%s"
          % ([j.source_index for j in mine.items], [j.severity_rank for j in mine.items],
             [j.source_index for j in others.items], [j.severity_rank for j in others.items]))

    assert [j.source_index for j in mine.items] == [0], "내 계정 뉴스가 ①에 있지 않다."
    assert [j.source_index for j in others.items] == [1], (
        "타 계정 초고심각 뉴스가 ③이 아니라 ① 쪽으로 넘어왔다(정렬이 바구니를 가로질렀다)."
    )
    # 바구니 순서 자체가 ①→②→③ 이므로, 화면 순서상 ③의 99가 ①의 10보다 위에 오지 않는다.
    order = [b.key for b in baskets]
    assert order.index("mine") < order.index("others"), "바구니 표시 순서가 ①<③ 이 아니다."


def test_within_basket_sorted_by_severity():
    """바구니 '안'에서는 심각도 내림차순 정렬이 유지된다."""
    js = [
        _j(0, 30, account="매출채권"),
        _j(1, 88, account="매출채권"),
        _j(2, 55, account="매출채권"),
    ]
    mine = _basket(classify_and_rank(js, viewer_account="매출채권"), "mine")
    ranks = [j.severity_rank for j in mine.items]
    print("\n[within] ① ranks=%s" % ranks)
    assert ranks == sorted(ranks, reverse=True), f"바구니 내 심각도 정렬이 깨졌다: {ranks}"


def test_no_hard_filter_total_preserved():
    """계정 하드 필터 금지: 어떤 뉴스도 삭제되지 않고 세 바구니 합이 입력과 같다."""
    js = [
        _j(0, 90, account="매출채권"),
        _j(1, 80, account_abstained=True),
        _j(2, 70, account="재고자산"),
        _j(3, 60, account="장기차입금"),
    ]
    for viewer in (None, "매출채권", "존재하지않는계정군"):
        baskets = classify_and_rank(js, viewer_account=viewer)
        total = sum(len(b.items) for b in baskets)
        print("[no_filter] viewer=%r → 총 %d건 (입력 %d)" % (viewer, total, len(js)))
        assert total == len(js), (
            f"viewer={viewer!r}: 바구니 합계 {total} ≠ 입력 {len(js)}. 어떤 뉴스가 삭제됐다(하드 필터)."
        )
