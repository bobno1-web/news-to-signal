"""
tests/invariants/test_direction.py

불변식(방향성): 같은 사건의 '악재판(수사 진행)'과 '해소판(무혐의)'을 나란히 놓았을 때,
악재판이 해소판보다 상대적으로 높게(먼저 봐야 하게) 랭크되어야 한다.
그리고 '소송/횡령' 같은 단어가 들어갔다는 이유로 무혐의(해소)를 악재로 뒤집으면 안 된다(원칙 1).

케이스는 가상 기업 '한밭전자'. 동일 사건(전 재무임원의 회삿돈 유용 의혹)을
두 국면(수사 착수 / 무혐의 처분)으로 서술한다.
"""

from tests.invariants._harness import Case, fmt, judge_cases, rank_position

_COMPANY = "한밭전자"
_ADVERSE = Case(
    key="수사",
    title="(가상)한밭전자 전 재무임원, 회삿돈 유용 의혹으로 검찰 수사 착수",
    snippet="검찰이 한밭전자 전 재무담당 임원이 법인 자금을 개인 용도로 빼돌린 정황을 잡고 "
            "이번 주 수사에 착수했다고 밝혔다. 회사 측은 내부 자금 흐름을 다시 들여다보고 있다.",
)
_RESOLVED = Case(
    key="무혐의",
    title="(가상)한밭전자 전 재무임원 회삿돈 유용 의혹, 검찰 무혐의 처분",
    snippet="한밭전자 전 재무담당 임원의 법인 자금 유용 의혹에 대해 검찰이 무혐의로 사건을 종결했다. "
            "조사 결과 개인적 유용으로 볼 근거가 없다고 판단했다.",
)


def test_direction():
    by_key, ranked = judge_cases(_COMPANY, [_ADVERSE, _RESOLVED])
    adv, res = by_key["수사"], by_key["무혐의"]

    print("\n[test_direction]")
    print("  입력 악재판(수사):", _ADVERSE.title)
    print("  입력 해소판(무혐의):", _RESOLVED.title)
    print("  기대: rank(수사) > rank(무혐의), 수사=악재, 무혐의=호재/중립")
    print("  실제 수사 :", fmt(adv))
    print("  실제 무혐의:", fmt(res))
    print("  정렬 위치: 수사=%d, 무혐의=%d (1=최상단)"
          % (rank_position(ranked, adv.source_index), rank_position(ranked, res.source_index)))

    # 핵심 불변식: 악재판이 해소판보다 위(더 큰 severity_rank).
    assert adv.severity_rank > res.severity_rank, (
        f"방향 불변식 실패: 수사(rank={adv.severity_rank})가 무혐의(rank={res.severity_rank})보다 "
        f"높지 않다. 해소를 악재만큼/그 이상으로 본 것."
    )
    # 방향 라벨: 무혐의를 악재로 뒤집지 않아야 한다.
    assert res.direction in ("호재", "중립"), f"무혐의를 '{res.direction}'로 판단(해소를 악재로 뒤집음)"
    assert adv.direction == "악재", f"수사를 '{adv.direction}'로 판단(악재여야 함)"
    # 단계 인식: 무혐의는 해소 단계로 인식.
    assert res.stage == "무혐의/취하", f"무혐의 stage='{res.stage}'"
