"""
tests/invariants/test_magnitude_unknown.py

불변식(규모 미상 ≠ 작음): '금액 미상 + 진행 중 부정' 사건이 '사소한 확정 사건'보다
높게 랭크되어야 한다. 금액이 안 잡힌다는 이유로 진행 중 횡령을 바닥에 깔면,
가장 위험한 사건을 놓친다(magnitude-rules.md 핵심, 원칙 4).

케이스는 가상 기업 '세종바이오'.
- (미상+부정) 내부 횡령 정황으로 감사 착수, 피해 규모는 아직 파악 중.
- (사소+확정) 본사 로비 인테리어 교체에 3,200만원 집행.
두 사건 모두 '금액이 재무제표 대비 비율로는 안 잡힌다'는 점은 같지만, 하나는
상한을 모르는 미상(경보)이고 다른 하나는 명백히 작은 확정 지출이다.
"""

from tests.invariants._harness import Case, fmt, judge_cases, rank_position

_COMPANY = "세종바이오"
_UNKNOWN = Case(
    key="미상횡령",
    title="(가상)세종바이오, 내부 자금 횡령 정황 포착돼 자체 감사 착수",
    snippet="세종바이오가 일부 자금이 비정상적으로 유출된 정황을 발견하고 외부 회계법인을 통한 "
            "특별감사에 들어갔다. 피해 규모는 아직 파악되지 않았으며 조사가 진행 중이다.",
)
_TRIVIAL = Case(
    key="사소인테리어",
    title="(가상)세종바이오, 본사 1층 로비 인테리어 교체",
    snippet="세종바이오가 본사 1층 로비 인테리어를 새로 단장했다. 이번 교체에는 약 3,200만원이 "
            "집행됐다고 회사 관계자가 전했다.",
)


def test_magnitude_unknown():
    by_key, ranked = judge_cases(_COMPANY, [_UNKNOWN, _TRIVIAL])
    unk, tri = by_key["미상횡령"], by_key["사소인테리어"]

    print("\n[test_magnitude_unknown]")
    print("  입력 미상+부정:", _UNKNOWN.title)
    print("  입력 사소+확정:", _TRIVIAL.title)
    print("  기대: rank(미상횡령) > rank(사소인테리어), 미상횡령 mag.size='미상'(≠작다), 사소 mag.size='작다'")
    print("  실제 미상횡령:", fmt(unk))
    print("  실제 사소    :", fmt(tri))
    print("  정렬 위치: 미상횡령=%d, 사소=%d (1=최상단)"
          % (rank_position(ranked, unk.source_index), rank_position(ranked, tri.source_index)))

    # 핵심: 미상+부정이 사소+확정보다 위.
    assert unk.severity_rank > tri.severity_rank, (
        f"규모 미상 불변식 실패: 미상횡령(rank={unk.severity_rank})이 "
        f"사소인테리어(rank={tri.severity_rank})보다 높지 않다. 미상을 작음처럼 바닥에 깖."
    )
    # 미상을 '작다'로 처리하지 않았는가 (가장 위험한 오류).
    assert unk.magnitude.size == "미상", (
        f"진행 중 횡령의 규모를 '{unk.magnitude.size}'로 판단. '미상'이어야 한다(미상≠작음)."
    )
    # 진행 중 부정은 본질적 위험도가 켜져야 규모 없이도 상단에 온다.
    assert unk.intrinsic_risk is True, "진행 중 횡령인데 intrinsic_risk=False (본질적 위험도 미인식)"
    # 사소 확정 지출은 미상이 아니라 '작다'로.
    assert tri.magnitude.size == "작다", f"사소 인테리어 규모='{tri.magnitude.size}'(작다여야 함)"
