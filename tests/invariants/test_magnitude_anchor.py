"""
tests/invariants/test_magnitude_anchor.py  (검증방 소유 — 루프3)

검증 2: 규모 앵커. 이번 루프 핵심 위험 두 가지를 겨냥한다.
1) 가짜 정밀: "수백억대" 같은 모호·범위 금액을 특정 숫자로 확정해 비율을 계산하면 실패.
   정답은 amount_is_clear=False → ratio_pct=None, size='미상' 유지.
2) 분모 고정표(하드코딩): 분모(매출액/자산총계/자기자본)가 사건유형 고정표가 아니라
   맥락 이해로 달라지는지. 같은 회사·같은 금액대라도 사건 성격이 다르면 분모가 달라야 한다.
그리고 비율 정합(분자÷분모)이 코드에서 정확히 계산되는지, 미상≠작음이 유지되는지 본다.

실제 API 호출(가상 기업 + 합성 재무숫자). conftest 가 키를 주입한다.
"""

from tests.invariants._harness import Case, judge_cases, quote_traceable

# 합성 재무 규모(원): 매출 2조 / 자산총계 3조 / 자기자본 1.2조. (가상 기업 — DART에 없음)
_FIN = {"매출액": 2_000_000_000_000, "자산총계": 3_000_000_000_000, "자기자본": 1_200_000_000_000}


def _mag_line(j) -> str:
    m = j.magnitude
    return (f"size={m.size}/{m.certainty} denom={m.denominator!r} amount={m.amount_krw} "
            f"clear={m.amount_is_clear} ratio_pct={m.ratio_pct} quote={m.amount_quote!r}")


# ── 1) 가짜 정밀: 범위/모호 금액은 비율로 확정되지 않는다 ──────────────────────
_RANGE_AMOUNT = Case(
    key="범위금액",
    title="(가상)오성전자, 협력사 대금 분쟁으로 수백억대 손실 발생 추정",
    snippet="오성전자가 협력사와의 대금 분쟁으로 수백억원대의 손실이 발생한 것으로 추정된다. "
            "정확한 손실 규모는 아직 확정되지 않았다고 회사 측은 밝혔다.",
)


def test_fake_precision_range_amount():
    by_key, _ = judge_cases("오성전자", [_RANGE_AMOUNT], accounts=[], financials=_FIN)
    j = by_key["범위금액"]
    print("\n[test_fake_precision] %s" % _mag_line(j))

    # 핵심: 범위 금액을 특정 수치로 확정하지 않는다 → 비율 계산 금지.
    assert j.magnitude.amount_is_clear is False, (
        f"'수백억대' 범위 금액을 명확(amount_is_clear=True)으로 표시했다(가짜 정밀의 씨앗). "
        f"amount_krw={j.magnitude.amount_krw}"
    )
    assert j.magnitude.ratio_pct is None, (
        f"모호 금액에 비율({j.magnitude.ratio_pct})을 붙였다 — 가짜 정밀(원칙 3·4)."
    )
    # 금액이 안 잡혔다고 '작음'으로 깔지 않는다(미상 유지, 미상≠작음).
    assert j.magnitude.size != "작다", "범위 손실 금액을 '작다'로 처리(미상≠작음 위반 가능)."


# ── 2) 분모는 맥락 의존(고정표 아님): 사건 성격 따라 분모가 달라진다 ────────────
_EQUITY_EVENT = Case(
    key="자본_유상증자",
    title="(가상)한울화학, 자본잠식 우려 속 3000억원 규모 유상증자 결정",
    snippet="한울화학이 자본잠식 우려가 커지자 3,000억원 규모의 유상증자를 결정했다고 공시했다. "
            "자본을 확충해 재무구조를 개선하려는 조치다.",
)
_REVENUE_EVENT = Case(
    key="매출_공급계약",
    title="(가상)한울화학, 최대 고객사와 연 5000억원 규모 장기 공급계약 체결",
    snippet="한울화학이 최대 고객사와 연간 5,000억원 규모의 장기 공급계약을 체결했다고 밝혔다. "
            "회사 연매출에서 큰 비중을 차지하는 대형 계약이다.",
)


def test_denominator_is_contextual():
    # 같은 회사·같은 금액대(수천억)지만 사건 성격이 다르다 → 분모가 달라져야 한다(LLM 판단).
    by_key, _ = judge_cases("한울화학", [_EQUITY_EVENT, _REVENUE_EVENT], accounts=[], financials=_FIN)
    eq, rev = by_key["자본_유상증자"], by_key["매출_공급계약"]
    print("\n[test_denominator_contextual]")
    print("  자본_유상증자: %s" % _mag_line(eq))
    print("  매출_공급계약: %s" % _mag_line(rev))

    # 분모가 사건 성격에 따라 갈린다 = 고정 단일 분모(하드코딩)가 아님을 보인다.
    assert eq.magnitude.denominator != rev.magnitude.denominator, (
        f"서로 다른 성격의 사건에 같은 분모를 썼다(맥락 무시/고정표 의심): "
        f"자본사건 denom={eq.magnitude.denominator!r}, 매출사건 denom={rev.magnitude.denominator!r}"
    )
    # 각 분모에 근거(denominator_reason)가 붙어야 한다(가설, 원칙 3).
    assert eq.magnitude.denominator_reason.strip(), "자본 사건 분모 선택에 근거가 없다."
    assert rev.magnitude.denominator_reason.strip(), "매출 사건 분모 선택에 근거가 없다."


# ── 3) 비율 정합: 명확한 단일 금액 → 코드가 분자÷분모를 정확히 계산 ────────────
_CLEAR_AMOUNT = Case(
    key="명확투자",
    title="(가상)대진重공업, 신규 물류센터에 1500억원 투자 확정",
    snippet="대진重공업이 신규 물류센터 건립에 1,500억원을 투자하기로 확정했다고 공시했다.",
)


def test_ratio_consistency_clear_amount():
    by_key, _ = judge_cases("대진重공업", [_CLEAR_AMOUNT], accounts=[], financials=_FIN)
    j = by_key["명확투자"]
    m = j.magnitude
    print("\n[test_ratio_consistency] %s" % _mag_line(j))

    assert m.amount_is_clear is True, "명확한 단일 금액(1500억원)을 불명확으로 표시했다."
    assert m.amount_krw == 150_000_000_000, (
        f"1500억원을 amount_krw={m.amount_krw} 로 표기(150000000000 이어야). 단위 변환 오류."
    )
    # 금액 근거는 원문에서 추적돼야 한다(반날조).
    assert quote_traceable(m.amount_quote, _CLEAR_AMOUNT), (
        f"금액 근거 문장이 원문에서 추적되지 않는다: {m.amount_quote!r}"
    )
    # 코드가 채운 비율이 '분자÷분모×100'과 정확히 일치해야 한다(LLM이 아니라 코드 산술).
    assert m.denominator in _FIN, f"분모 '{m.denominator}' 가 재무숫자에 없다(계산 불가)."
    expected = m.amount_krw / _FIN[m.denominator] * 100.0
    assert m.ratio_pct is not None and abs(m.ratio_pct - expected) < 1e-9, (
        f"비율 정합 실패: ratio_pct={m.ratio_pct}, 기대={expected} (분자 {m.amount_krw} ÷ 분모 "
        f"{m.denominator}={_FIN[m.denominator]})"
    )
