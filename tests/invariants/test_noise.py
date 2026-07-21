"""
tests/invariants/test_noise.py

불변식(노이즈): 순수 마케팅/홍보/중립 기사는 저심각도로 처리되어야 한다.
재무·감사에 닿지 않는 뉴스는 관련성 1차 관문에서 걸러져(원칙 6·심각도 축 1),
실질 위험 뉴스보다 아래로 내려가야 한다. 단, 삭제하는 게 아니라 아래로만 내린다(원칙 7).

이번 루프는 계정 연결이 없어(account_hint 항상 빈 값) '가짜 계정 연결' 자체는
아직 발생하지 않는다. 그래서 이 루프의 노이즈 불변식은 '마케팅 기사가 저심각도로
가라앉고 실질 위험보다 아래에 오는가'로 검증한다.

케이스는 가상 기업 '가온식품'.
- (노이즈) 신제품 출시 기념 SNS 이벤트/경품 — 순수 마케팅.
- (대조군, 실질 위험) 주채권은행이 여신 회수를 통보 — 유동성 직격.
"""

from tests.invariants._harness import Case, fmt, judge_cases, rank_position

_COMPANY = "가온식품"
_NOISE = Case(
    key="마케팅",
    title="(가상)가온식품, 신제품 '가온 프로틴바' 출시 기념 SNS 이벤트",
    snippet="가온식품이 신제품 프로틴바 출시를 기념해 SNS 참여 이벤트를 연다. 게시물을 공유한 "
            "고객 추첨을 통해 제품 세트와 커피 기프티콘을 증정한다.",
)
_CONTROL = Case(
    key="유동성위험",
    title="(가상)가온식품 주채권은행, 여신 회수 통보",
    snippet="가온식품의 주채권은행이 만기 도래 여신을 연장하지 않고 회수하겠다고 통보했다. "
            "회사는 단기 유동성 확보에 비상이 걸렸다.",
)


def test_noise():
    by_key, ranked = judge_cases(_COMPANY, [_NOISE, _CONTROL])
    noise, ctrl = by_key["마케팅"], by_key["유동성위험"]

    print("\n[test_noise]")
    print("  입력 노이즈:", _NOISE.title)
    print("  입력 대조군:", _CONTROL.title)
    print("  기대: 마케팅 relevant=False & 저심각도, 유동성위험보다 아래로 정렬")
    print("  실제 마케팅  :", fmt(noise))
    print("  실제 대조군  :", fmt(ctrl))
    print("  정렬 위치: 마케팅=%d, 유동성위험=%d (1=최상단)"
          % (rank_position(ranked, noise.source_index), rank_position(ranked, ctrl.source_index)))

    # 마케팅은 관련성 관문 탈락(재무제표 미접촉).
    assert noise.relevant is False, (
        f"순수 마케팅 기사를 relevant=True로 판단. 관련성 관문(재무 실질)에서 걸러져야 함."
    )
    # 노이즈가 실질 위험보다 위로 오면 정렬이 깨진 것.
    assert noise.severity_rank < ctrl.severity_rank, (
        f"노이즈 불변식 실패: 마케팅(rank={noise.severity_rank})이 "
        f"유동성위험(rank={ctrl.severity_rank})보다 낮지 않다."
    )
    # 저심각도의 절대 상한(느슨). 마케팅이 중간 이상이면 감정/화제성에 반응한 신호.
    assert noise.severity_rank <= 30, (
        f"마케팅 severity_rank={noise.severity_rank} 가 저심각도(≤30)를 넘음. 화제성에 반응한 의심."
    )
    # 실질 위험은 방향이 악재로 잡혀야 대조가 성립.
    assert ctrl.relevant is True and ctrl.direction == "악재", f"대조군 판단 이상: {fmt(ctrl)}"
