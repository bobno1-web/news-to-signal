"""
tests/invariants/test_evidence_defense.py  (검증방 소유 — 루프2 독립 적대검증)

검증 1: '근거 강제'가 실제로 코드로 닫혔는지, 그리고 '지어낸 근거로 빈칸을 채우는
우회'가 없는지 독립적으로 시험한다. 개발방의 test_evidence_required 를 신뢰하지 않고
검증방이 직접 적대 입력을 parse_batch 에 먹인다(원칙 10 — 채점자와 응시자 분리).

무-API 결정론적 단위 테스트. parse_batch 의 _enforce_evidence 방어선을 겨냥한다.
"""

from src.judge.schema import parse_batch


def _row(**over) -> dict:
    row = {
        "source_index": 0, "relevant": True, "relevance_reason": "(x)",
        "direction": "악재", "direction_reason": "(x)", "stage": "해당없음",
        "intrinsic_risk": False, "intrinsic_risk_reason": "(x)",
        "magnitude": {"size": "미상", "certainty": "불명", "reason": "(x)"},
        "evidence": [{"quote": "원문 근거 문장", "field": "스니펫"}],
        "confidence": "보통", "abstained": False, "abstain_reason": "",
        "severity_rank": 50, "one_line_reason": "(x)",
        "account_links": [], "account_abstained": True, "account_abstain_reason": "(x)",
    }
    row.update(over)
    return row


def test_evidence_defense_blocks_empty_list():
    """근거 배열이 비면(가장 단순한 우회) 강제 기권으로 강등돼야 한다."""
    j = parse_batch({"judgments": [_row(evidence=[])]})[0]
    print("\n[empty_list] abstained=%s ev=%d reason=%r" % (j.abstained, len(j.evidence), j.abstain_reason[:50]))
    assert j.abstained is True, "근거 없는 relevant=True 판단이 비-기권으로 통과(방어선 뚫림)."
    assert len(j.evidence) == 0, "강등 시 근거를 지어내 채우면 날조."


def test_evidence_defense_blank_quote_bypass():
    """
    핵심 적대검증(검증 1 — '지어낸 근거로 빈칸을 채우는 우회'):
    evidence 를 빈/공백 quote 1건으로 채우면 len(evidence)==1 이 되어 현재 방어선
    (_enforce_evidence 의 len==0 검사)을 우회한다. 이 판단은 사실상 근거가 없는데도
    비-기권으로 통과하고, render 는 '근거: "" ' 를 표시하게 된다.

    기대(옳은 동작): 공백/빈 quote 는 근거로 인정되지 않아 이 판단도 강제 기권돼야 한다.
    실제 동작을 실물로 드러낸다. 통과(=우회 성립)하면 이 테스트는 실패하고, 그것이
    방어선의 빈틈을 문서화한다.
    """
    blank = parse_batch({"judgments": [_row(source_index=0, evidence=[{"quote": "   ", "field": "제목"}])]})[0]
    empty = parse_batch({"judgments": [_row(source_index=1, evidence=[{"quote": "", "field": "스니펫"}])]})[0]

    print("\n[blank_quote] abstained=%s quote=%r" % (blank.abstained, blank.evidence[0].quote))
    print("[empty_quote] abstained=%s quote=%r" % (empty.abstained, empty.evidence[0].quote))

    # 실질적 근거 유무는 'quote 가 공백이 아닌가'로 봐야 한다. 공백뿐인 근거는 근거가 아니다.
    def has_real_evidence(jj) -> bool:
        return any(e.quote.strip() for e in jj.evidence)

    assert blank.abstained is True or has_real_evidence(blank), (
        "공백 quote 근거가 방어선을 우회했다: relevant=True·비-기권인데 실질 근거가 없다. "
        "근거 날조/빈칸채움에 방어선이 열려 있다(_enforce_evidence 가 len 만 보고 quote.strip() 을 안 본다)."
    )
    assert empty.abstained is True or has_real_evidence(empty), (
        "빈 quote 근거가 방어선을 우회했다(위와 동일 빈틈)."
    )


def test_evidence_defense_leaves_abstained_and_noise_alone():
    """정직한 기권·무관(relevant=False)은 근거 없이도 강등 대상이 아니다(과잉 강제 금지)."""
    honest_abstain = _row(source_index=0, abstained=True, evidence=[], abstain_reason="얇은 헤드라인")
    noise = _row(source_index=1, relevant=False, abstained=False, evidence=[],
                 direction="중립", magnitude={"size": "해당없음", "certainty": "불명", "reason": "무관"})
    judged = {j.source_index: j for j in parse_batch({"judgments": [honest_abstain, noise]})}
    print("\n[abstain] abstained=%s  [noise] abstained=%s"
          % (judged[0].abstained, judged[1].abstained))
    assert judged[0].abstained is True
    assert judged[1].abstained is False, "무관 뉴스를 근거 없다고 강제 기권하면 안 된다(노이즈≠기권)."
