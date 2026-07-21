"""
tests/invariants/test_screening.py  (검증방 소유 — 루프6)

불변식(스크리닝 거짓 탈락 방지 — 원칙 7·1): 기본 화면 표시 필터(default_visible)와
그 앞의 관련성 판단(LLM)이 '진짜 재무·감사 악재'를 화면에서 떨구면 안 된다.

이 프로젝트에는 별도의 '스크리닝 게이트'가 없다 — 관련성은 풀 7축 판단 안의 relevant
필드이고, '접힘'은 표시 계층 default_visible(direction/relevant/stage/abstained 를 읽음)
이 정한다. 그러므로 '악재가 떨어지지 않는가'는 (판단 + 표시필터)를 함께 태워 검증한다.

- 명백한 악재(워크아웃·분식·소송·감사의견·횡령·디폴트·공장화재)는 기본 화면에 남는다.
- 위험 해소(무혐의)는 남는다(호재라도 stage=무혐의/취하 → 위험해소).
- 순수 무관(봉사·광고)만 접히되, 삭제가 아니라 '무관'으로 접힘(보존·접근 가능).
- 같은 표현('공장 화재') 다른 사건 2건이 하나로 합쳐지지 않는다(중복 오병합 없음).
- 입력이 증발하지 않는다(수집=판단, 누락 금지).

실제 Anthropic API 호출(과금·캐시). 키 없으면 skip 아닌 실패로 둔다(검증 못 함을 드러냄).
"""

from src.api.serialize import default_visible, fold_reason, is_resolution
from tests.invariants._harness import Case, SAMPLE_ACCOUNTS, judge_cases

_CO = "가상검증제조"

# 명백한 재무·감사 악재 — 반드시 기본 화면에 남아야 한다(거짓 탈락 금지).
_BAD = [
    Case("워크아웃",
         "(가상)가상검증제조, 주채권은행에 워크아웃 신청…자본잠식 심화",
         "가상검증제조가 유동성 악화로 주채권은행에 워크아웃(기업개선작업)을 신청했다. 자본잠식이 심화돼 상장폐지 우려도 제기된다."),
    Case("분식정황",
         "(가상)가상검증제조 전 재무담당 임원 '매출 부풀리기 지시받았다' 폭로",
         "전직 재무담당 임원이 회사가 가공매출로 실적을 부풀리도록 지시했다고 주장했다. 감독당국이 회계처리 적정성 점검에 착수했다."),
    Case("대규모소송",
         "(가상)가상검증제조, 3천억원 규모 손해배상 소송 피소",
         "핵심 거래처가 공급계약 위반을 이유로 가상검증제조를 상대로 3천억원 규모의 손해배상 청구 소송을 제기했다."),
    Case("감사의견",
         "(가상)가상검증제조 외부감사인, 계속기업 불확실성에 '의견거절' 검토",
         "외부감사인이 가상검증제조의 계속기업 존속능력에 중대한 불확실성이 있다며 감사의견 거절 가능성을 내비쳤다."),
    Case("횡령",
         "(가상)가상검증제조 자금팀장, 회삿돈 200억 횡령 혐의 구속",
         "가상검증제조 자금팀장이 회사 자금 약 200억원을 빼돌린 혐의로 구속됐다. 내부통제 부실 정황이 드러났다."),
    Case("디폴트우려",
         "(가상)가상검증제조 회사채 차환 실패…디폴트 우려 확산",
         "만기 도래한 회사채 차환에 실패하면서 가상검증제조의 채무불이행(디폴트) 우려가 커지고 있다."),
]
_BORDER = [
    Case("파업",
         "(가상)가상검증제조 노조 총파업 3주째…주력공장 가동 중단",
         "임금협상 결렬로 노조가 3주째 총파업에 들어가 주력공장 가동이 멈췄다. 납기 지연과 매출 차질이 우려된다."),
    Case("신용전망",
         "(가상)신용평가사, 가상검증제조 등급전망 '부정적' 하향",
         "한 신용평가사가 가상검증제조의 신용등급 전망을 '안정적'에서 '부정적'으로 낮췄다. 차입여건 악화 가능성을 지적했다."),
]
_NOISE = [
    Case("봉사활동",
         "(가상)가상검증제조 대표, 연말 연탄나눔 봉사활동 참여",
         "가상검증제조 대표가 지역 저소득층을 위한 연탄나눔 봉사활동에 참여했다."),
    Case("광고모델",
         "(가상)가상검증제조, 신제품 광고모델에 인기 아이돌 발탁",
         "가상검증제조가 신제품 홍보를 위해 인기 아이돌 그룹을 광고모델로 발탁했다."),
]
_RESOLVE = [
    Case("무혐의종결",
         "(가상)가상검증제조 분식 의혹 '혐의없음' 종결…리스크 해소",
         "검찰이 가상검증제조의 회계 분식 의혹을 수사한 결과 혐의가 없다고 판단해 사건을 종결했다. 불확실성이 해소됐다."),
]
_DUP = [
    Case("부산화재",
         "(가상)가상검증제조 부산공장 화재로 생산라인 전소",
         "가상검증제조 부산공장에서 화재가 발생해 생산라인이 전소됐다. 재고자산·유형자산 손실이 예상된다."),
    Case("울산화재",
         "(가상)가상검증제조 울산공장서도 화재…이틀 새 두 번째",
         "가상검증제조 울산공장에서도 화재가 발생했다. 부산공장 화재와는 별개 사건으로, 원인은 조사 중이다."),
]
# 순서 고정(캐시 재현). 판단 payload 는 이 순서·내용으로 결정된다.
_ALL = _BAD + _BORDER + _NOISE + _RESOLVE + _DUP


def _judge():
    return judge_cases(_CO, _ALL, accounts=SAMPLE_ACCOUNTS)


def test_clear_bad_news_never_dropped():
    """명백한 재무·감사 악재는 기본 화면에서 절대 떨어지지 않는다(거짓 탈락=원칙 7 위반)."""
    by_key, _ = _judge()
    for c in _BAD:
        j = by_key[c.key]
        assert default_visible(j), (
            f"거짓 탈락: 명백 악재 '{c.key}' 가 기본 화면에서 사라졌다 "
            f"(relevant={j.relevant}, dir={j.direction}, abst={j.abstained}, 접힘사유={fold_reason(j)})."
        )


def test_risk_resolution_visible():
    """위험 해소(무혐의/취하)는 호재라도 기본 화면에 남는다(리스크 신호이므로)."""
    by_key, _ = _judge()
    j = by_key["무혐의종결"]
    assert is_resolution(j) and default_visible(j), f"위험 해소가 화면에서 사라졌다: {j.stage}/{j.direction}"


def test_borderline_not_dropped_as_irrelevant():
    """재무 연관이 약해도 가능성 있는 경계 케이스를 '무관'으로 탈락시키지 않는다(느슨 게이트)."""
    by_key, _ = _judge()
    for c in _BORDER:
        j = by_key[c.key]
        assert not (not default_visible(j) and fold_reason(j) == "재무·감사 무관"), (
            f"경계 케이스 '{c.key}' 를 '재무·감사 무관'으로 탈락시켰다(게이트가 느슨하지 않음)."
        )


def test_pure_noise_folds_but_preserved():
    """순수 무관(봉사·광고)은 접히되(relevant=False→무관), 삭제가 아니라 접근 가능해야 한다."""
    by_key, ranked = _judge()
    for c in _NOISE:
        j = by_key[c.key]
        assert not default_visible(j) and fold_reason(j) == "재무·감사 무관", \
            f"무관 뉴스 '{c.key}' 처리 이상: visible={default_visible(j)}, 사유={fold_reason(j)}"
        # 삭제 금지: 접힌 항목도 판단 결과(ranked)에 그대로 존재한다.
        assert any(x.source_index == j.source_index for x in ranked), "무관 뉴스가 결과에서 증발했다(원칙 7)."


def test_similar_events_not_merged_and_none_evaporate():
    """같은 표현 다른 사건 2건이 합쳐지지 않고, 입력이 하나도 증발하지 않는다."""
    by_key, ranked = _judge()
    b, u = by_key["부산화재"], by_key["울산화재"]
    assert b.source_index != u.source_index, "다른 사건(부산/울산 화재)이 하나로 병합됐다."
    assert len(ranked) == len(_ALL), f"뉴스 증발: 입력 {len(_ALL)} ≠ 판단 {len(ranked)} (수집=판단 위반)."
