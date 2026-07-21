"""
src/judge/engine.py

판단 계층: 뉴스 여러 건을 한 요청에 묶어(batch, 비용 절약) LLM에 보내고,
건별 판단(schema.Judgment)을 받는다.

- 원칙·축은 docs/ 에서 읽어 프롬프트에 주입한다(원칙은 코드가 아니라 문서로 산다).
  docs/ 는 헌법이므로 주입해도 되지만, reference/ 는 절대 import 하지 않는다(원칙 9).
- 키가 없으면 임의 값으로 대체하지 않고 명확한 에러로 멈춘다(원칙 4).
- 모델: claude-opus-4-8, adaptive thinking, structured outputs(구조화 출력).
- 계정 연결: DART 계정 후보 목록(accounts)을 '울타리'로 함께 주입한다. 판단 주체는
  LLM이고, DART 목록에 없는 계정은 파싱 후 enforce_account_fence 가 제거한다(schema).
- 판단 캐시: 같은 (모델·프롬프트·입력)에 대한 재호출을 막는다(.cache/judge/).
"""

import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic

from src.judge.schema import (
    BATCH_SCHEMA, Judgment, apply_magnitude_anchor, enforce_account_fence,
    make_missing_judgment, parse_batch,
)
from src.sources.base import NewsItem

_MODEL = "claude-opus-4-8"
_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system.txt"
_DOC_PRINCIPLES = _ROOT / "docs" / "principles.md"
_DOC_AXES = _ROOT / "docs" / "severity-axes.md"
_DOC_MAGNITUDE = _ROOT / "docs" / "magnitude-rules.md"
_CACHE_DIR = _ROOT / ".cache" / "judge"

_CHUNK = 15  # 한 배치에 담는 최대 뉴스 건수(출력 토큰 상한 관리)
_MAX_CONCURRENCY = 6  # 동시에 띄우는 청크 판단 수(고정 상한). 순차→병렬은 오케스트레이션만 바꾼다.


class JudgeConfigError(RuntimeError):
    """API 키 미설정 등 설정 오류. 임의 값 대체 없이 즉시 멈춘다(원칙 4)."""


def _build_system_prompt() -> str:
    """docs/ 원칙·축·규모규칙을 프롬프트 템플릿에 주입해 시스템 프롬프트를 만든다."""
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        principles=_DOC_PRINCIPLES.read_text(encoding="utf-8"),
        axes=_DOC_AXES.read_text(encoding="utf-8"),
        magnitude=_DOC_MAGNITUDE.read_text(encoding="utf-8"),
    )


def _build_user_payload(company: str, items: list[NewsItem], accounts: list[str],
                        financials: dict) -> str:
    lines = [f"회사명: {company}", ""]
    if accounts:
        lines.append(
            "[이 회사의 계정과목 후보 목록 — DART 제공, '울타리']\n"
            "아래 계정군 안에서만 연결하라. 목록에 없는 계정을 지어내지 마라(없으면 계정 기권).\n"
            "정확 계정명 매칭이 아니라 계정군 수준으로 느슨하게 본다."
        )
        lines.append(", ".join(accounts))
        lines.append("")
    else:
        lines.append("[계정 후보 목록 없음] 계정 연결을 시도하지 말고 모두 계정 기권으로 둔다.")
        lines.append("")
    if financials:
        lines.append("[이 회사의 재무 규모 — DART, 규모 비율의 분모 후보(원)]")
        for k in ("매출액", "자산총계", "자기자본"):
            if k in financials:
                lines.append(f"  {k}: {financials[k]:,}")
        lines.append("사건 금액을 어느 분모와 비교할지는 사건을 이해해 네가 고른다(고정표 없음).")
        lines.append("비율은 네가 계산하지 마라 — 명확한 단일 금액(amount_krw)과 분모(denominator)만 내면 코드가 나눈다.")
        lines.append("")
    else:
        lines.append("[재무 규모 없음] 분모가 없어 비율을 못 낸다 — denominator='해당없음', 비율은 생략된다.")
        lines.append("")
    lines.append("뉴스 목록:")
    for it in items:
        lines.append(f"[번호 {it.source_index}] 날짜: {it.published}")
        lines.append(f"  제목: {it.title}")
        lines.append(f"  스니펫: {it.snippet}")
    return "\n".join(lines)


def _cache_key(system: str, payload: str) -> str:
    h = hashlib.sha256()
    h.update(_MODEL.encode("utf-8"))
    h.update(b"\x00")
    h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def _judge_chunk(client: anthropic.Anthropic, system: str, company: str,
                 items: list[NewsItem], accounts: list[str], financials: dict,
                 on_usage=None) -> list[Judgment]:
    payload = _build_user_payload(company, items, accounts, financials)
    key = _cache_key(system, payload)
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        text = cache_file.read_text(encoding="utf-8")
    else:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": BATCH_SCHEMA},
            },
            # 프롬프트 캐싱(루프14 C-2): system(원칙+축+규모규칙+템플릿)은 모든 청크·모든 회사에서
            # 바이트 동일한 고정 프리픽스다. cache_control 로 걸면 첫 청크가 캐시를 쓰고, 이후 청크는
            # 캐시 적중(입력의 0.1x)으로 읽는다. Opus 4.8 최소 캐시 프리픽스 4096 토큰을 이 system 은
            # 넉넉히 넘는다. 회사별로 바뀌는 계정 울타리·재무·뉴스는 system 뒤 user 에 있어 캐시를 깨지
            # 않는다. 실제 적중은 usage.cache_read_input_tokens 로 계측한다.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": payload}],
        )
        # on_usage: 순수 텔레메트리(비용 측정용). 판단 입력·출력·로직에 영향 없음.
        if on_usage is not None:
            on_usage(_MODEL, resp.usage)
        text = next(b.text for b in resp.content if b.type == "text")
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(text, encoding="utf-8")

    judgments = parse_batch(json.loads(text))          # 원칙 3 방어선(근거 강제)은 여기서
    enforce_account_fence(judgments, accounts)          # 울타리(없는 계정 제거)
    apply_magnitude_anchor(judgments, financials)       # 규모 앵커(코드가 비율 계산)
    return judgments


def _reconcile_returns(items: list[NewsItem], judgments: list[Judgment]) -> list[Judgment]:
    """
    증발 방어(루프9, 원칙 7·4): 배치 판단이 입력보다 적게(또는 어긋나게) 돌아오면 뉴스가
    조용히 사라진다. 여기서 '입력 items 전부에 정확히 1건의 Judgment'가 되도록 대조·복구한다.

    - 누락(모델이 안 돌려준 source_index): 삭제하지 말고 강제기권(make_missing_judgment)으로
      보존해 화면·집계에 '미판단'으로 드러낸다. 근거·판단을 지어내지 않는다(원칙 3).
    - 중복(같은 source_index 2번 반환): 첫 판단만 채택(둘째부터 버림) — 한 뉴스가 두 번
      담기지 않게. 버린 게 아니라 같은 건의 중복이다.
    - 유령(입력에 없는 source_index): 대응 원문이 없어 채택하지 않는다(가짜 항목 방지).
    출력은 입력 items 순서로 재조립한다(결정론 — 병렬/순차·재실행 동일). 불일치가 있으면
    조용히 넘어가지 않고 stderr 로 경고한다(블랙박스 금지). 이 함수는 판단 '내용'을 바꾸지
    않는다 — 반환 집합의 보존만 강제한다(7축 로직·프롬프트 불변).
    """
    expected = [it.source_index for it in items]
    expected_set = set(expected)
    by_index: dict[int, Judgment] = {}
    duplicate: list[int] = []
    phantom: list[int] = []
    for j in judgments:
        si = j.source_index
        if si not in expected_set:
            phantom.append(si)                 # 입력에 없는 번호 — 원문 없음, 채택 안 함
            continue
        if si in by_index:
            duplicate.append(si)               # 중복 반환 — 첫 것만 유지
            continue
        by_index[si] = j

    missing = [si for si in expected if si not in by_index]
    final = [by_index.get(si) or make_missing_judgment(si) for si in expected]

    if missing or duplicate or phantom:
        # 조용히 넘어가지 않는다(원칙 7). 누락은 위 강제기권으로 화면·집계에 남고, 여기선 로그로도 알린다.
        print(
            f"[judge_items] 배치 반환 불일치 검출 — 입력 {len(items)}건, 판단 반환 {len(judgments)}건: "
            f"누락(미판단 보존) {len(missing)}{missing or ''} · 중복 {len(duplicate)}{duplicate or ''} · "
            f"유령(입력에 없는 번호) {len(phantom)}{phantom or ''}",
            file=sys.stderr,
        )
    return final


def judge_items(company: str, items: list[NewsItem], accounts: list[str] | None = None,
                financials: dict | None = None, *, chunk: int = _CHUNK,
                on_usage=None) -> list[Judgment]:
    """
    뉴스들을 chunk 단위로 묶어 LLM으로 판단하고, 전체 Judgment 리스트를 반환한다.
    accounts: DART 계정 후보 목록(울타리). 없으면 계정 연결은 전부 기권 처리된다.
    financials: DART 재무 규모(분모 후보). 없으면 규모 비율은 계산되지 않는다(미상/작음 유지).
    각 NewsItem.source_index 는 호출 전에 부여되어 있어야 한다(pipeline 담당).
    on_usage: 선택적 텔레메트리 콜백(비용 측정용). 판단 로직/입출력에는 영향이 없다.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise JudgeConfigError(
            "ANTHROPIC_API_KEY 환경변수가 없습니다. "
            "임의 값으로 대체하지 않고 중단합니다(원칙 4)."
        )
    if not items:
        return []

    accounts = accounts or []
    financials = financials or {}
    # max_retries: 동시성 아래에서 일시적 429는 SDK 내장 지수 백오프로 재시도한다.
    # 클라이언트(httpx 커넥션 풀)는 스레드 안전이라 한 인스턴스를 스레드가 공유해도 된다.
    client = anthropic.Anthropic(max_retries=5)
    system = _build_system_prompt()

    # on_usage 는 이제 여러 워커 스레드에서 호출될 수 있다. 사용자 싱크(list.append/
    # file.write 등)가 스레드 안전이 아닐 수 있으므로 Lock 으로 호출을 직렬화해 감싼다.
    # on_usage 가 없으면 감싸지 않는다(콜백 없음 → None 그대로 전달).
    safe_on_usage = None
    if on_usage is not None:
        _usage_lock = threading.Lock()

        def safe_on_usage(model, usage, _cb=on_usage, _lock=_usage_lock):
            with _lock:
                _cb(model, usage)

    # 결정론(병렬 출력 == 순차 출력, 바이트 단위 동일) 논거:
    #   1) 독립성: 각 청크의 판단은 자기 payload(items[start:start+chunk] + system)에만
    #      의존한다. 청크 간 공유 상태가 없어 실행이 겹쳐도 서로의 결과를 바꾸지 못한다.
    #   2) 같은 캐시 키: _cache_key 는 (모델·system·payload)로만 결정되므로 청크가
    #      어떤 순서/스레드로 돌든 캐시 적중·본문은 순차와 동일하다.
    #   3) 순서 재조립: 완료 순서가 아니라 원래 start 인덱스 오름차순으로 이어붙인다.
    #   따라서 결과 Judgment 리스트는 순차 실행과 정확히 같다.
    starts = list(range(0, len(items), chunk))
    by_start: dict[int, list[Judgment]] = {}
    with ThreadPoolExecutor(max_workers=min(_MAX_CONCURRENCY, len(starts))) as pool:
        futures = {
            pool.submit(_judge_chunk, client, system, company,
                        items[start:start + chunk], accounts, financials,
                        on_usage=safe_on_usage): start
            for start in starts
        }
        # 실패한 청크는 조용히 건너뛰지 않고 예외를 전파한다(원칙 4: 데이터 무손실).
        for fut, start in futures.items():
            by_start[start] = fut.result()

    results: list[Judgment] = []
    for start in starts:                       # 원래 인덱스 오름차순으로 재조립
        results.extend(by_start[start])
    # 증발 방어(원칙 7·4): 입력 items 전부에 정확히 1건이 되도록 대조·복구(누락→강제기권 보존).
    return _reconcile_returns(items, results)
