"""
tests/invariants/test_abstention.py  (검증방 소유 — 루프2 구현)

불변식(계정 기권 — 핵심): 계정을 특정할 수 없는 얇은/모호한 헤드라인을 주면,
시스템은 특정 계정을 '확신'하지 말고 기권해야 한다. 정답은 "계정 특정 불가(일반
리스크)"다(account-linkage.md·원칙 4). 억지 특정은 날조다.

기권이 '늘 켜져 있어' 통과하는 것을 막기 위해, 같은 배치에 '계정이 명확한 대조군'을
함께 넣어 그때는 연결이 나오는지도 본다(기권이 진짜 판단이지 게으름이 아님을 확인).

실제 API 호출(가상 기업 + 합성 울타리). conftest 가 키를 주입한다.
"""

from tests.invariants._harness import SAMPLE_ACCOUNTS, Case, judge_cases, quote_traceable

_COMPANY = "하람산업"

_AMBIG_EARNINGS = Case(
    key="모호_실적기대",
    title="(가상)하람산업, 다음 주 2분기 실적 발표 앞두고 투자자 관심 집중",
    snippet="하람산업이 다음 주 2분기 실적을 발표한다. 시장에서는 실적의 방향을 두고 관심이 모이고 있다.",
)
_AMBIG_UNCERTAINTY = Case(
    key="모호_경영불확실",
    title="(가상)하람산업, 대내외 경영 환경 불확실성에 촉각",
    snippet="하람산업이 대내외 경영 환경의 불확실성이 커지는 가운데 사업 전략 전반을 점검하고 있다고 밝혔다.",
)
_CLEAR_RECEIVABLE = Case(
    key="명확_대손",
    title="(가상)하람산업, 주요 납품처 법정관리로 납품대금 회수 불투명",
    snippet="하람산업의 주요 납품처가 법정관리에 들어가면서 그동안 쌓인 납품대금 회수가 불투명해졌다. "
            "회사는 대금 회수 지연에 따른 손실 가능성을 검토 중이다.",
)


def test_abstention():
    cases = [_AMBIG_EARNINGS, _AMBIG_UNCERTAINTY, _CLEAR_RECEIVABLE]
    by_key, _ = judge_cases(_COMPANY, cases, accounts=SAMPLE_ACCOUNTS)

    print("\n[test_abstention] (울타리 %d개 계정군)" % len(SAMPLE_ACCOUNTS))
    for c in cases:
        j = by_key[c.key]
        links = [f"{l.account_group}<-\"{l.quote}\"" for l in j.account_links]
        print(f"  {c.key}: relevant={j.relevant} account_abstained={j.account_abstained} "
              f"links={links} | {j.account_abstain_reason[:40]}")

    amb_e, amb_u, clear = by_key["모호_실적기대"], by_key["모호_경영불확실"], by_key["명확_대손"]

    # 모호한 두 건: 특정 계정을 확신하면 실패. 연결이 비어 있거나 명시적 계정 기권이어야 한다.
    for j, key in ((amb_e, "모호_실적기대"), (amb_u, "모호_경영불확실")):
        assert not j.account_links, (
            f"[{key}] 얇/모호한 헤드라인에서 특정 계정을 확신함(links={[l.account_group for l in j.account_links]}). "
            f"정답은 계정 기권(일반 리스크)."
        )
        assert j.account_abstained is True, f"[{key}] 계정 연결도 없는데 account_abstained=False (정직한 기권 표기 누락)."

    # 대조군(명확): 기권이 '게으름'이 아님을 보인다 — 이때는 근거 동반 연결이 나와야 한다.
    assert clear.account_links, "명확한 대손 사건인데도 계정 연결이 하나도 안 나왔다(기권이 늘 켜진 것 아닌지)."
    # 나온 연결의 근거 문장은 원문에서 실제로 추적돼야 한다(반날조 — 근거를 지어내지 않았는가).
    traceable = [l for l in clear.account_links if quote_traceable(l.quote, _CLEAR_RECEIVABLE)]
    print("  명확_대손 연결 근거 추적: %d/%d 가 원문에서 확인됨"
          % (len(traceable), len(clear.account_links)))
    assert traceable, (
        "명확 사건의 계정 연결 근거가 원문에서 추적되지 않는다(근거 문장을 지어냈을 가능성). "
        f"quotes={[l.quote for l in clear.account_links]}"
    )
