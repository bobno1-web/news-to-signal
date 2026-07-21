"""
src/judge/schema.py

판단(triage) 출력 형식. LLM은 뉴스 1건마다 아래 구조를 낸다.
핵심: 결론이 아니라 '가설 + 근거'(원칙 3), 모르면 기권(원칙 4),
축은 점수 공식이 아니라 판단을 드러내는 창문(원칙 8).

severity_rank는 LLM이 사건을 이해해 내린 '종합 정렬 신호'다. 코드가 축에
가중치를 곱해 계산한 값이 아니며(원칙 1·8), 사람에게 표시하지 않는다(원칙 5 —
내부 계산값 비노출). 화면에는 숫자가 아니라 one_line_reason(말로 된 이유)이 나간다.

계정 연결(account_links)은 '결론'이 아니라 '가설 + 근거'다(account-linkage.md).
각 연결은 반드시 원문 근거 문장을 동반하고, DART 후보 목록 안에서만 인정된다
(울타리 — 없는 계정을 지어내지 못하게). 특정 불가하면 계정은 기권한다(원칙 4).

이 모듈은 두 개의 '물리적 방어선'을 집행한다:
- 근거 없는 비-기권 판단은 통과할 수 없다(원칙 3). parse_batch가 강제 기권으로 강등.
- DART 후보에 없는 계정은 통과할 수 없다(울타리). enforce_account_fence가 제거.
"""

from dataclasses import dataclass, field


@dataclass
class Evidence:
    """원문(제목/스니펫)에서 발췌한 근거. 근거를 못 대는 판단은 폐기한다(원칙 3)."""

    quote: str    # 발췌 문장/구
    field: str    # "제목" | "스니펫"


@dataclass
class AccountLink:
    """뉴스↔계정군 연결 '가설'(결론 아님). DART 후보 울타리 안에서만 인정된다."""

    account_group: str   # 관련 있어 보이는 계정군(DART 후보 목록 중 하나, 느슨/계정군 수준)
    quote: str           # 이 연결의 근거 문장(원문 발췌) — 없으면 연결은 폐기(원칙 3)
    field: str           # "제목" | "스니펫"
    reason: str          # 왜 이 계정군과 관련 있어 보이는지(가설, 단정 아님)


@dataclass
class Magnitude:
    """
    규모 축. '미상'은 '작음'이 아니다(magnitude-rules.md 핵심).

    규모 앵커(루프3): 사건 금액(분자)이 '명확한 단일 수치'로 잡히고 LLM이 분모를 고르면,
    코드가 비율(분자÷분모×100)을 계산해 concrete anchor 로 붙인다. 범위·모호 금액은
    amount_is_clear=False 로 남겨 비율을 계산하지 않는다(가짜 정밀 금지, 원칙 3·4).
    ratio_pct 는 LLM이 아니라 코드가 채운다(LLM은 나눗셈을 하지 않는다).
    """

    size: str        # "크다" | "작다" | "미상" | "해당없음"(관련성 관문 탈락 시)
    certainty: str   # "확정" | "불명"
    reason: str
    # ── 규모 앵커(분모는 LLM이 사건 이해로 선택, 고정표 아님 — 원칙 1) ──
    denominator: str = ""          # "매출액" | "자산총계" | "자기자본" | "해당없음"
    denominator_reason: str = ""   # 왜 이 분모로 재야 하는지(가설)
    amount_krw: int = 0            # 사건 금액(원). 명확한 단일 수치일 때만 >0.
    amount_is_clear: bool = False  # 명확한 단일 수치인가(범위·모호면 False → 비율 계산 안 함)
    amount_quote: str = ""         # 금액 근거 문장(원문 발췌 — 없으면 비율 신뢰 안 함)
    ratio_pct: float | None = None # 코드가 계산한 비율(%). 계산 불가면 None(미상/작음 유지).


@dataclass
class Judgment:
    """뉴스 1건에 대한 판단."""

    source_index: int              # 입력 뉴스 번호
    relevant: bool                 # (a) 재무·감사에 실질 영향이 있는가(관련성 = 1차 관문)
    relevance_reason: str          # (a) 이유
    direction: str                 # (b) "악재" | "호재" | "중립" (무혐의·취하 = 리스크 해소)
    direction_reason: str
    stage: str                     # 확실성/단계: 혐의|수사|기소|확정|무혐의/취하|해당없음|미상
    intrinsic_risk: bool           # 본질적 위험도: 금액과 무관하게 신뢰·존속을 흔드는가
    intrinsic_risk_reason: str
    magnitude: Magnitude           # (e) 규모 상태
    evidence: list[Evidence]       # (c) 원문 근거 문장
    confidence: str                # (d) "높음" | "보통" | "낮음"
    abstained: bool                # (d) 명시적 기권 여부
    abstain_reason: str
    severity_rank: int             # 내부 정렬 신호(0~100). 홀리스틱 판단, 공식 아님, 미표시.
    one_line_reason: str           # 1층 표시용 한 줄 이유(등급·숫자 아님)

    # 계정 연결(가설). 비면 '미배정'(라우팅 2번 바구니). 지어낸 계정은 울타리가 제거한다.
    account_links: list[AccountLink] = field(default_factory=list)
    account_abstained: bool = False        # 계정 특정 불가(정직한 기권 — 정상이고 자주 나온다)
    account_abstain_reason: str = ""
    # 미판단(증발 방어, 루프9): 모델이 배치 응답에서 이 건을 안 돌려줘 '판단 없음'인 상태.
    # 삭제(증발) 대신 강제기권으로 보존해 화면·집계에 드러낸다(원칙 7·4). 지어낸 판단 아님.
    unjudged: bool = False
    # 주체성(루프10): '이 기사가 이 회사를 다루고 있는가'를 LLM이 기사를 이해해 셋 중
    # 하나로 표기한 값이다(단독주체/복수주체/타사언급). 8번째 심각도 축이 아니며 severity_rank
    # 를 바꾸지 않는다(원칙 8) — 표시층이 이 값을 '읽어' C(타사언급)만 하단으로 내린다.
    # 기본값 ""(미분류)는 강등하지 않는다(애매/구덤프/미판단은 놓치지 않게 상단 취급, 원칙 7).
    subject_role: str = ""
    subject_role_reason: str = ""          # 특히 C: 기사 주체가 누구이며 이 회사가 어떻게 언급됐는지(근거는 기사에서, 원칙 3)
    # 감사중요(루프11): '이 사건이 감사인의 확인·후속절차를 요하는가'를 방향과 독립적으로 LLM이
    # 판단한 값(예/아니오). 심각도 축이 아니며 severity_rank 를 바꾸지 않는다(원칙 8) — 주체성과
    # 동일 패턴: 판단층은 값만 표기, 표시층이 읽어 배치. True 면 방향이 '중립'이어도 기본 화면에
    # 노출된다(중대한 감사 포인트가 중립 종합으로 접히던 공백 보완). '계정 붙으면 True' 같은 코드
    # 규칙이 아니라(원칙 1) LLM 이 기사를 이해해 정한다. 과다 노출 방지: 실제 후속확인이 필요한
    # 사건에만 True(단순 동향·가격 조정·홍보성은 False).
    audit_attention: bool = False
    audit_attention_reason: str = ""       # 왜 감사인의 확인·후속절차가 필요한지(근거는 기사에서, 원칙 3)
    # 사건군(루프13): '이 기사가 다루는 하나의 진행 중 사안(사건군)'을 LLM이 기사를 이해해 붙인
    # 라벨이다. 같은 사안의 연속 보도(예: 담합 심의 착수→과징금 확정→소송전)는 같은 라벨을 공유하고,
    # 별개 사안은 다른 라벨(또는 빈 값)이다. 심각도 축이 아니며 severity_rank 를 바꾸지 않는다(원칙 8)
    # — 표시층이 이 라벨로 '대표 1건만 펼쳐 두고 나머지는 그 아래로 접어' 같은 사안의 도배를 줄인다
    # (삭제 아님, 펼치면 전부 보임 — 원칙 7). prescreen 의 중복 병합(같은 기사 묶기)과 다르다: 서로
    # 다른 국면(심의/확정/소송)을 뭉개지 않고 '각각 남기되 한 군으로 묶어 보인다'. 애매하면 다른
    # 사안으로 둔다(오병합=증발 금지, 원칙 7·1). 빈 값="" = 사건군 없음(단독 사건).
    event_group: str = ""


# ── 주체성 3분류(루프10) ─────────────────────────────────────────────────────
# '이 기사가 이 회사를 다루고 있는가'. LLM이 기사를 이해해 내리는 값이지 키워드·제목
# 매칭이 아니다(원칙 1). 심각도 축이 아니므로 severity_rank 계산에 쓰지 않는다(원칙 8).
# 표시층은 이 값을 '읽어' C(타사언급)만 하단으로 내린다(강등이지 삭제·접힘이 아님 — 원칙 7).
SUBJECT_SOLE = "단독주체"        # A: 이 회사가 기사의 주인공
SUBJECT_MULTI = "복수주체"       # B: 여러 회사를 나란히 다루되 이 회사도 실질 내용으로 그중 하나
SUBJECT_PERIPHERAL = "타사언급"  # C: 다른 주체의 기사에 부수적으로 스친 언급(이 회사를 다루는 기사가 아님)
_SUBJECT_ROLES = (SUBJECT_SOLE, SUBJECT_MULTI, SUBJECT_PERIPHERAL)


def is_peripheral_mention(j: Judgment) -> bool:
    """C(타사 기사 내 언급)인가 — 표시 강등의 유일한 조건. '명백한 C'에서만 True 다.
    빈 값/미분류/구덤프/미판단/A/B 는 모두 False(애매하면 B로 두어 놓치지 않는다 — 원칙 7).
    표시층·정렬층이 이 값을 읽어 C만 하단으로 옮긴다. 판단 내용은 바꾸지 않는다."""
    return getattr(j, "subject_role", "") == SUBJECT_PERIPHERAL


# ── LLM 구조화 출력(structured outputs)용 JSON 스키마 ───────────────────────────
# Anthropic output_config.format 에 그대로 전달한다. 모든 object에
# additionalProperties: false 와 required 를 둔다(구조화 출력 요구사항).
#
# 주의: evidence 에 minItems 를 두지 않는다. 기권/무관 판단은 근거가 없을 수 있고,
# 스키마 일괄 강제는 기권 항목에 근거를 '지어내게' 만들 수 있어 오히려 원칙 3에
# 어긋난다(날조 금지). 대신 '비-기권 실질 판단엔 근거 필수'라는 조건부 규칙을
# parse_batch(코드)에서 집행한다. 같은 이유로 account_group 은 enum 으로 고정하지
# 않는다(회사마다 계정명이 다름 — 원칙 2). 후보 울타리는 코드가 집행한다.

_EVIDENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "quote": {"type": "string"},
        "field": {"type": "string", "enum": ["제목", "스니펫"]},
    },
    "required": ["quote", "field"],
}

_ACCOUNT_LINK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "account_group": {"type": "string"},
        "quote": {"type": "string"},
        "field": {"type": "string", "enum": ["제목", "스니펫"]},
        "reason": {"type": "string"},
    },
    "required": ["account_group", "quote", "field", "reason"],
}

# 출력 다이어트(루프14 C-1): 화면에 노출되지 않는 서술 필드는 생성 자체를 중단해 출력 토큰을 아낀다.
# magnitude.reason 은 render()·judgment_view() 어디에도 표시되지 않으므로 스키마에서 뺀다(모델이 안
# 뱉음). 규모의 '근거'는 amount_quote(원문 인용)이며 그건 유지한다(원칙 3 — 근거는 줄이지 않는다).
# dataclass Magnitude.reason 필드는 유지(구 덤프·테스트 하위호환) — parse_batch 가 .get 기본값 ""로 채운다.
_MAGNITUDE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "size": {"type": "string", "enum": ["크다", "작다", "미상", "해당없음"]},
        "certainty": {"type": "string", "enum": ["확정", "불명"]},
        "denominator": {"type": "string", "enum": ["매출액", "자산총계", "자기자본", "해당없음"]},
        "denominator_reason": {"type": "string"},    # 분모 선택 근거(비율 앵커 2층에 표시 — 유지)
        "amount_krw": {"type": "integer"},          # 원 단위 정수. 명확한 단일 수치일 때만 >0.
        "amount_is_clear": {"type": "boolean"},      # 범위·모호면 false (가짜 정밀 금지)
        "amount_quote": {"type": "string"},          # 금액 근거 문장(원문 발췌 — 근거이므로 유지)
    },
    "required": [
        "size", "certainty",
        "denominator", "denominator_reason", "amount_krw", "amount_is_clear", "amount_quote",
    ],
}

_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_index": {"type": "integer"},
        "relevant": {"type": "boolean"},
        # relevance_reason·direction_reason·intrinsic_risk_reason 제거(루프14 C-1): 화면(render·
        # judgment_view) 어디에도 노출되지 않는 서술이라 생성 자체를 중단해 출력 토큰을 아낀다. 판단의
        # 검증 수단(원칙 3·5)은 evidence(원문 인용)·one_line_reason 이며 그건 유지한다. 축의 결론값
        # (relevant·direction·intrinsic_risk 불리언/enum)은 판단·정렬에 쓰이므로 그대로 둔다.
        "direction": {"type": "string", "enum": ["악재", "호재", "중립"]},
        "stage": {
            "type": "string",
            "enum": ["혐의", "수사", "기소", "확정", "무혐의/취하", "해당없음", "미상"],
        },
        "intrinsic_risk": {"type": "boolean"},
        "magnitude": _MAGNITUDE_SCHEMA,
        "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA},
        "confidence": {"type": "string", "enum": ["높음", "보통", "낮음"]},
        "abstained": {"type": "boolean"},
        "abstain_reason": {"type": "string"},
        "severity_rank": {"type": "integer"},
        "one_line_reason": {"type": "string"},
        # 주체성(루프10): '이 기사가 이 회사를 다루는가' 3분류 + 근거. 심각도 축 아님(원칙 8).
        "subject_role": {"type": "string", "enum": ["단독주체", "복수주체", "타사언급"]},
        "subject_role_reason": {"type": "string"},
        # 감사중요(루프11): 방향과 독립된 '감사인 확인·후속절차 요망' 여부 + 근거. 심각도 축 아님(원칙 8).
        "audit_attention": {"type": "boolean"},
        "audit_attention_reason": {"type": "string"},
        # 사건군(루프13): 같은 진행 중 사안의 연속 보도를 묶는 라벨(표시 도배 완화용). 심각도 축 아님(원칙 8).
        "event_group": {"type": "string"},
        "account_links": {"type": "array", "items": _ACCOUNT_LINK_SCHEMA},
        "account_abstained": {"type": "boolean"},
        "account_abstain_reason": {"type": "string"},
    },
    "required": [
        "source_index", "relevant", "direction",
        "stage", "intrinsic_risk",
        "magnitude", "evidence", "confidence", "abstained", "abstain_reason",
        "severity_rank", "one_line_reason", "subject_role", "subject_role_reason",
        "audit_attention", "audit_attention_reason", "event_group",
        "account_links", "account_abstained", "account_abstain_reason",
    ],
}

BATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"judgments": {"type": "array", "items": _ITEM_SCHEMA}},
    "required": ["judgments"],
}


# ── 파싱 + 물리적 방어선 집행 ─────────────────────────────────────────────────

_FORCED_ABSTAIN_NOTE = (
    "[강제 기권] 실질 위험을 주장(relevant=True)했으나 원문 근거 문장이 없어 "
    "원칙 3에 따라 기권 처리함(근거 못 대는 판단은 폐기)."
)


_MISSING_JUDGMENT_NOTE = (
    "판단 응답 누락 — 모델이 이 뉴스를 배치 응답에서 돌려주지 않았습니다. "
    "시스템이 판단하지 못함(증발 아님, 보존됨) — 사람 확인/재시도 필요."
)


def make_missing_judgment(source_index: int) -> Judgment:
    """
    배치가 돌려주지 않은(누락된) 입력 뉴스를 '증발' 대신 강제기권 판단으로 보존한다
    (원칙 7 숨기지 않음 · 원칙 4 누락 금지). 판단을 지어내지 않는다(원칙 3):
    근거 없음(evidence=[]), 계정 기권, 규모 미상, 방향 중립. 화면·집계에 '미판단'으로
    드러나도록 unjudged=True 와 ⚠ 이유 문구를 담는다. severity_rank 는 지어낸 심각도가
    아니라 0(내부값·미표시)이다.
    """
    return Judgment(
        source_index=source_index,
        relevant=False,
        relevance_reason=_MISSING_JUDGMENT_NOTE,
        direction="중립",
        direction_reason=_MISSING_JUDGMENT_NOTE,
        stage="미상",
        intrinsic_risk=False,
        intrinsic_risk_reason="",
        magnitude=Magnitude(size="미상", certainty="불명", reason="판단 응답 누락"),
        evidence=[],                       # 근거를 지어내지 않는다(원칙 3)
        confidence="낮음",
        abstained=True,
        abstain_reason=_MISSING_JUDGMENT_NOTE,
        severity_rank=0,
        one_line_reason="⚠ 시스템이 이 뉴스를 판단하지 못함(판단 응답 누락 — 사람 확인 필요)",
        account_links=[],
        account_abstained=True,
        account_abstain_reason="판단 누락으로 계정 연결 불가",
        unjudged=True,
    )


def _enforce_evidence(judgments: list[Judgment]) -> list[Judgment]:
    """
    원칙 3의 물리적 방어선: 근거 없는 '비-기권 실질 판단'은 통과시키지 않는다.
    abstained=False && relevant=True && 실질 근거 없음 → 강제 기권으로 강등한다.
    '실질 근거'는 개수가 아니라 내용으로 본다 — 공백/빈 quote 는 근거로 세지 않는다
    (계정 울타리 enforce_account_fence 의 quote.strip 검사와 기준 통일).
    (폐기 대신 강등: 원칙 7 — 숨기지 않는다. 항목은 남되 '판단 기권'으로 정직하게 표기.)
    근거를 지어내 채우지 않는다(그게 날조다).
    """
    for j in judgments:
        has_evidence = any(e.quote.strip() for e in j.evidence)  # 공백/빈 quote 는 근거로 안 셈
        if (not j.abstained) and j.relevant and not has_evidence:
            j.abstained = True
            j.abstain_reason = (
                f"{j.abstain_reason} {_FORCED_ABSTAIN_NOTE}".strip()
                if j.abstain_reason else _FORCED_ABSTAIN_NOTE
            )
    return judgments


def parse_batch(data: dict) -> list[Judgment]:
    """
    구조화 출력 JSON(dict)을 Judgment 리스트로 변환하고, 원칙 3 방어선을 집행한다.
    (계정 울타리는 회사별 후보가 필요하므로 enforce_account_fence 에서 별도 집행.)
    """
    result: list[Judgment] = []
    for row in data.get("judgments", []):
        mag = row["magnitude"]
        result.append(
            Judgment(
                source_index=row["source_index"],
                relevant=row["relevant"],
                # 루프14 C-1: relevance/direction/intrinsic_risk 의 *_reason 은 스키마에서 뺐다(미표시).
                # 신규 응답엔 없고 구 덤프·테스트 dict 엔 있을 수 있으므로 .get 기본값 ""로 받는다.
                relevance_reason=row.get("relevance_reason", ""),
                direction=row["direction"],
                direction_reason=row.get("direction_reason", ""),
                stage=row["stage"],
                intrinsic_risk=row["intrinsic_risk"],
                intrinsic_risk_reason=row.get("intrinsic_risk_reason", ""),
                magnitude=Magnitude(
                    size=mag["size"], certainty=mag["certainty"], reason=mag.get("reason", ""),
                    # 새 앵커 필드는 .get 기본값으로 — 구필드(3개)만 담긴 손수 만든 dict도 파싱되게.
                    denominator=mag.get("denominator", ""),
                    denominator_reason=mag.get("denominator_reason", ""),
                    amount_krw=int(mag.get("amount_krw", 0) or 0),
                    amount_is_clear=bool(mag.get("amount_is_clear", False)),
                    amount_quote=mag.get("amount_quote", ""),
                ),
                evidence=[Evidence(quote=e["quote"], field=e["field"]) for e in row["evidence"]],
                confidence=row["confidence"],
                abstained=row["abstained"],
                abstain_reason=row["abstain_reason"],
                severity_rank=row["severity_rank"],
                one_line_reason=row["one_line_reason"],
                # 주체성(루프10): 구필드만 담긴 손수 만든 dict/구덤프도 파싱되게 .get 기본값 ""(미분류→강등 안 함).
                subject_role=row.get("subject_role", ""),
                subject_role_reason=row.get("subject_role_reason", ""),
                # 감사중요(루프11): 구 dict/구덤프엔 없을 수 있음 → .get 기본값 False(노출 강제 안 함).
                audit_attention=bool(row.get("audit_attention", False)),
                audit_attention_reason=row.get("audit_attention_reason", ""),
                # 사건군(루프13): 구 dict/구덤프엔 없음 → .get 기본값 ""(사건군 없음 → 도배 묶기 안 함).
                event_group=row.get("event_group", ""),
                account_links=[
                    AccountLink(
                        account_group=a["account_group"], quote=a["quote"],
                        field=a["field"], reason=a["reason"],
                    )
                    for a in row.get("account_links", [])
                ],
                account_abstained=row.get("account_abstained", False),
                account_abstain_reason=row.get("account_abstain_reason", ""),
            )
        )
    return _enforce_evidence(result)


def _norm_account(name: str) -> str:
    """계정명 정규화(느슨 매칭용): 공백 제거. 계정군 수준 비교를 위한 최소 정리."""
    return "".join(name.split())


def enforce_account_fence(judgments: list[Judgment], allowed: list[str]) -> list[Judgment]:
    """
    울타리(account-linkage.md): DART 후보 목록에 없는 계정으로의 연결은 제거한다.
    LLM이 없는 계정을 지어내지 못하게 막는 물리적 방어선. 근거 문장이 빈 연결도 제거한다
    (원칙 3 — 계정 연결도 근거를 동반해야 한다). 정확 매칭이 아니라 계정군 수준(느슨).
    연결이 모두 제거되면 그 뉴스는 '미배정'으로 남는다(라우팅 2번 바구니, 숨기지 않음).
    allowed 가 비어 있으면(계정 목록 확보 실패) 연결을 신뢰할 근거가 없으므로 전부 제거한다.
    """
    allowed_norm = [_norm_account(a) for a in allowed if a and a.strip()]

    def _in_fence(group: str) -> bool:
        g = _norm_account(group)
        if len(g) < 2:
            return False
        for a in allowed_norm:
            if g == a or (len(g) >= 2 and (g in a or a in g)):
                return True
        return False

    for j in judgments:
        kept = [
            link for link in j.account_links
            if link.quote.strip() and _in_fence(link.account_group)
        ]
        j.account_links = kept
    return judgments


_DENOMINATORS = ("매출액", "자산총계", "자기자본")


def apply_magnitude_anchor(judgments: list[Judgment], financials: dict) -> list[Judgment]:
    """
    규모 앵커: 사건 금액(분자)이 '명확한 단일 수치'이고 LLM이 분모를 골랐을 때만
    비율(분자÷분모×100)을 '코드가' 계산해 magnitude.ratio_pct 에 채운다.

    가짜 정밀 방어선(원칙 3·4): 금액이 불명확(amount_is_clear=False)하거나 금액 근거
    문장이 비어 있으면 계산하지 않는다 — 코드는 숫자를 지어내지 않고, LLM이 내린
    size(미상 등)를 그대로 둔다. '미상 ≠ 작음' 유지(미상을 비율로 덮어쓰지 않는다).
    financials: {"매출액": int, "자산총계": int, "자기자본": int}(원). 없으면 계산 불가.
    LLM은 나눗셈을 하지 않는다(분자·분모 선택만) — 산술은 결정론적으로 코드가 한다.
    """
    for j in judgments:
        m = j.magnitude
        m.ratio_pct = None
        if not m.amount_is_clear or m.amount_krw <= 0 or not m.amount_quote.strip():
            continue                       # 금액이 모호/무근거 → 비율 없음(미상 유지)
        if m.denominator not in _DENOMINATORS:
            continue                       # 분모 미선택/해당없음 → 계산 안 함
        denom = financials.get(m.denominator)
        if not denom or denom <= 0:
            continue                       # 분모 재무숫자 확보 실패 → 계산 안 함
        m.ratio_pct = m.amount_krw / denom * 100.0
    return judgments
