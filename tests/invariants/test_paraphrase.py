"""
tests/invariants/test_paraphrase.py

불변식(강건성): 같은 사실을 언론사마다 다르게 표현해도 판단이 뒤집히지 않아야 한다.
표현이 바뀌었다고 관련성·방향이 달라지거나 심각도 순위가 크게 요동치면,
그것은 사건을 이해한 게 아니라 문구에 반응한 것이다(원칙 1 위반의 신호).

케이스는 가상 기업 '대명해운'. 하나의 사실 — 최대 화주와의 장기 운송계약이
갱신되지 않아 내년부터 매출의 상당분이 빠진다 — 을 세 가지 표현으로 서술한다.
(감정적으로 밋밋하지만 매출을 직격하는 사건: 원칙 6의 전형)
"""

from tests.invariants._harness import Case, fmt, judge_cases

_COMPANY = "대명해운"
_CASES = [
    Case(
        key="표현1",
        title="(가상)대명해운, 최대 화주 대명물산과 장기 운송계약 연장 불발",
        snippet="대명해운이 최대 화주인 대명물산과의 장기 운송계약 연장에 실패했다. "
                "내년부터 해당 물량이 빠지면서 매출 공백이 불가피할 전망이다.",
    ),
    Case(
        key="표현2",
        title="(가상)대명해운 최대 고객 물량 이탈 확정… 매출 상당분 감소 전망",
        snippet="대명해운의 최대 고객사가 물량을 거둬들이기로 하면서 주력 계약이 종료된다. "
                "회사 매출에서 큰 비중을 차지하던 거래여서 내년 실적에 타격이 예상된다.",
    ),
    Case(
        key="표현3",
        title="(가상)대명해운, 주력 화주와 계약 갱신 무산으로 매출 기반 흔들려",
        snippet="대명해운이 주력 화주와의 운송계약을 갱신하지 못했다. 오랜 기간 매출을 떠받쳐온 "
                "핵심 거래가 끊기면서 향후 매출 규모가 줄어들 것으로 보인다.",
    ),
]

# 표현 차이로 인한 순위 요동 허용 폭. 이보다 크게 벌어지면 '판단이 뒤집혔다'로 본다.
_BAND = 30


def test_paraphrase():
    by_key, _ = judge_cases(_COMPANY, _CASES)
    js = [by_key[c.key] for c in _CASES]
    ranks = [j.severity_rank for j in js]

    print("\n[test_paraphrase]")
    for c, j in zip(_CASES, js):
        print(f"  {c.key}: {fmt(j)}")
    print(f"  기대: 세 표현 모두 relevant=True, dir=악재, severity_rank 폭≤{_BAND}")
    print(f"  실제 severity_rank들={ranks}, 폭={max(ranks) - min(ranks)}")

    # 관련성·방향이 표현에 따라 뒤집히면 안 된다.
    for c, j in zip(_CASES, js):
        assert j.relevant is True, f"[{c.key}] 매출 직격 사건을 relevant=False로 판단(관련성 뒤집힘)"
        assert j.direction == "악재", f"[{c.key}] direction='{j.direction}'(악재여야 함)"
    # 심각도 순위가 표현 차이로 크게 요동치면 안 된다.
    assert max(ranks) - min(ranks) <= _BAND, (
        f"패러프레이즈 불변식 실패: severity_rank 폭={max(ranks) - min(ranks)}>{_BAND}. "
        f"같은 사실인데 표현에 따라 심각도가 요동침. ranks={ranks}"
    )
