"""
tests/unit/test_schema_defenses.py  (개발방 소유 — 자기 방어선의 코드 단위 테스트)

원칙 10 경계: tests/invariants/ 는 검증방 전용(불변식 저작·판정)이다. 개발방이 자기
코드(스키마 방어선)를 확인하는 단위 테스트는 여기 tests/unit/ 에 둔다. (루프2에서
tests/invariants/ 에 두었던 test_account_fence·test_evidence_required 를 이리로 이동.)

모두 비-API 결정론적 단위 테스트다(과금 없음). 검증하는 방어선:
- _enforce_evidence: 근거 없는(공백 포함) 비-기권 실질 판단 → 강제 기권.
- enforce_account_fence: DART 후보 밖·무근거 계정 연결 제거.
- apply_magnitude_anchor: 명확한 금액+분모일 때만 비율 계산(가짜 정밀 금지).
"""

from src.judge.schema import (
    apply_magnitude_anchor, enforce_account_fence, parse_batch,
)


def _row(**over) -> dict:
    """스키마 형태의 판단 row 한 개(기본값 채움 + 오버라이드)."""
    row = {
        "source_index": 0,
        "relevant": True,
        "relevance_reason": "(테스트)",
        "direction": "악재",
        "direction_reason": "(테스트)",
        "stage": "해당없음",
        "intrinsic_risk": False,
        "intrinsic_risk_reason": "(테스트)",
        "magnitude": {"size": "미상", "certainty": "불명", "reason": "(테스트)"},
        "evidence": [{"quote": "원문 근거 문장", "field": "스니펫"}],
        "confidence": "보통",
        "abstained": False,
        "abstain_reason": "",
        "severity_rank": 50,
        "one_line_reason": "(테스트)",
        "account_links": [],
        "account_abstained": True,
        "account_abstain_reason": "(테스트)",
    }
    row.update(over)
    return row


def test_evidence_required():
    """근거 없는(빈 배열/공백 quote) 비-기권 실질 판단은 강제 기권으로 강등된다."""
    empty = _row(source_index=0, relevant=True, abstained=False, evidence=[])
    blank = _row(source_index=1, relevant=True, abstained=False,
                 evidence=[{"quote": "   ", "field": "제목"}])  # 공백 quote 우회 시도
    ok = _row(source_index=2, relevant=True, abstained=False,
              evidence=[{"quote": "실질 근거", "field": "제목"}])
    noise = _row(source_index=3, relevant=False, abstained=False, evidence=[],
                 direction="중립", magnitude={"size": "해당없음", "certainty": "불명", "reason": "무관"})

    judged = {j.source_index: j for j in parse_batch({"judgments": [empty, blank, ok, noise]})}

    assert judged[0].abstained is True, "빈 근거가 비-기권으로 통과(방어선 뚫림)."
    assert judged[1].abstained is True, "공백 quote 가 방어선을 우회했다(len 만 보고 strip 안 봄)."
    assert len(judged[1].evidence) == 1 and judged[1].evidence[0].quote == "   ", "근거를 지어내 채우면 날조."
    assert judged[2].abstained is False, "정상(실질 근거 동반) 판단을 건드렸다."
    assert judged[3].abstained is False, "무관(relevant=False)을 근거 없다고 강제 기권하면 안 된다."

    for j in judged.values():
        has_ev = any(e.quote.strip() for e in j.evidence)
        assert not (not j.abstained and j.relevant and not has_ev), (
            f"불변식 위반: source_index={j.source_index} 가 실질 근거 없이 비-기권 실질 판단으로 남았다."
        )


def test_account_fence():
    """DART 후보 밖·무근거 계정 연결은 제거된다(울타리)."""
    allowed = ["매출채권및기타채권", "재고자산", "장기차입금"]
    links = [
        {"account_group": "매출채권", "quote": "납품대금 회수 지연", "field": "스니펫", "reason": "가설"},
        {"account_group": "영업권", "quote": "손상 우려", "field": "스니펫", "reason": "가설"},
        {"account_group": "재고자산", "quote": "", "field": "스니펫", "reason": "가설"},  # 무근거
    ]
    j = parse_batch({"judgments": [_row(account_links=links, account_abstained=False)]})[0]
    enforce_account_fence([j], allowed)
    kept = {l.account_group for l in j.account_links}
    assert "매출채권" in kept, "후보 계정군과 느슨히 일치하는 연결이 제거됐다."
    assert "영업권" not in kept, "후보에 없는 계정(영업권)이 울타리를 통과했다."
    assert "재고자산" not in kept, "근거 문장 없는 연결이 통과했다."

    j2 = parse_batch({"judgments": [_row(account_links=links, account_abstained=False)]})[0]
    enforce_account_fence([j2], [])
    assert j2.account_links == [], "계정 후보가 없을 때 연결을 신뢰하면 안 된다(전부 제거)."


def _mag(**over) -> dict:
    m = {"size": "작다", "certainty": "확정", "reason": "x",
         "denominator": "자산총계", "denominator_reason": "x",
         "amount_krw": 0, "amount_is_clear": False, "amount_quote": ""}
    m.update(over)
    return m


def test_magnitude_anchor():
    """명확한 단일 금액+분모+근거일 때만 비율 계산. 모호/무근거/무분모는 미계산(가짜 정밀 금지)."""
    fin = {"매출액": 300_000_000_000_000, "자산총계": 450_000_000_000_000,
           "자기자본": 300_000_000_000_000}

    clear = _row(source_index=0, magnitude=_mag(
        denominator="자산총계", amount_krw=150_000_000_000, amount_is_clear=True,
        amount_quote="1500억원을 출연"))
    vague = _row(source_index=1, magnitude=_mag(
        size="미상", certainty="불명", denominator="자산총계", amount_krw=0,
        amount_is_clear=False, amount_quote=""))          # 범위·모호
    noquote = _row(source_index=2, magnitude=_mag(
        denominator="매출액", amount_krw=100_000_000_000, amount_is_clear=True,
        amount_quote="   "))                               # 근거 문장 없음

    js = parse_batch({"judgments": [clear, vague, noquote]})
    apply_magnitude_anchor(js, fin)
    by = {j.source_index: j for j in js}

    expected = 150_000_000_000 / 450_000_000_000_000 * 100  # ≈ 0.0333%
    assert by[0].magnitude.ratio_pct is not None and abs(by[0].magnitude.ratio_pct - expected) < 1e-9
    assert by[1].magnitude.ratio_pct is None, "모호 금액에 가짜 정밀 비율을 붙였다(원칙 3·4)."
    assert by[1].magnitude.size == "미상", "미상을 비율로 덮어썼다(미상≠작음)."
    assert by[2].magnitude.ratio_pct is None, "근거 문장 없는 금액으로 비율을 계산했다."

    js2 = parse_batch({"judgments": [clear]})
    apply_magnitude_anchor(js2, {})                        # 분모 없음
    assert js2[0].magnitude.ratio_pct is None, "재무 규모 없을 때 비율을 계산했다."
