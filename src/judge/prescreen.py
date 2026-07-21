"""
src/judge/prescreen.py  (루프6b — '입구' 깔때기, 7축 판단 앞단)

넓게 수집한 뉴스(대기업은 1000건 넘게 나온다)를 Opus 풀판단(7축) 앞에서 정리한다:
  1) 중복 정리(dedup): '같은 사건의 같은 국면'을 다룬 기사들만 한 대표로 묶는다. 같은 사안이라도
     진행 국면(단계·쟁점·확정성)이 다르면 별개로 두어 국면별 대표가 각각 판단을 받게 한다.
  2) 간이 관련성 관문(loose gate): 재무·감사와 '명백히' 무관한 것만 접는다.
대표만 Opus 로 보낸다. 접힌 것(같은 국면 중복·무관)은 삭제하지 않고 모두 보존한다(원칙 7).

원칙 준수(이 모듈의 존재 이유이자 한계):
- 원칙 1: 고정된 사건유형 목록·단계표·키워드·문자열 유사도 임계값으로 분류/병합하지 않는다.
  같은 발표(국면)인지, 무관인지는 '더 싼 모델(Haiku)이 기사를 이해해' 판단한다. 코드는 그
  이해 결과(event_id·relevant)를 묶기만 한다.
- 원칙 7: 아무것도 삭제하지 않는다. 관문은 느슨하게(애매하면 통과), 병합은 보수적으로
  (같은 발표·같은 국면인지 애매하면 합치지 않음 — 다른 사건이나 다른 국면을 하나로 합쳐
  하나를 없애지 않도록). 놓침을 줄이는 쪽으로 편향한다(놓침 > 오탐, 원칙 7).
- 표시층 분리: 같은 '사안'을 묶어 '보여주는' 것은 표시층 event_group(루프13)의 몫이다. 이 입구
  층은 '같은 국면 중복 제거'만 하고 국면을 소실시키지 않는다 — 두 층의 역할이 다르다.

규모 처리(대기업 1000+건):
- 1패스: 배치(≤120건)마다 Haiku 로 관련성 + '배치 안' 중복 군집 → 배치 대표.
- 2패스: 1패스 대표들만 모아 한 번 더 Haiku 로 '전역' 중복 군집(배치를 가로지르는 같은
  사건 — 예: 워크아웃 기사가 모든 배치에 흩어져 있는 것 — 을 하나로 묶는다).
- 배치가 커지면 군집 정확도가 떨어지므로 '과분할'(합쳐야 할 걸 못 합침)은 허용한다
  (안전 — Opus 를 조금 더 부를 뿐). '오병합'(다른 사건, 또는 같은 사안의 다른 국면을 합침)만
  프롬프트로 강하게 막는다 — 국면 오병합은 감사상 중대한 국면(예: 과징금 확정)을 증발시킨다.

주의(검증방 몫): 더 약한 모델이 앞단에서 관련성을 거르므로 '거짓 탈락'의 새 표면이 생긴다.
관문을 느슨히 두어 방어하지만, Haiku 관문에 대한 거짓탈락 불변식은 검증방이
tests/invariants/ 에 세워야 한다(원칙 10 — 채점자·응시자 분리).
"""

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from src.sources.base import NewsItem

_MODEL = "claude-haiku-4-5"
_ROOT = Path(__file__).resolve().parents[2]
_CACHE_DIR = _ROOT / ".cache" / "prescreen"
_MAX_ITEMS = 1300    # 수집 상한(그 너머는 접되 표기 — 숨기지 않음). 네이버 자체 상한(~1000+300).
_BATCH = 120         # 1패스 배치 크기(군집 신뢰도 유지)
_MAX_CONCURRENCY = 6  # 1패스에서 동시에 띄우는 _screen 호출 수(고정 상한). 오케스트레이션만 바꾼다.

_SYSTEM = (
    "너는 기업 뉴스의 '입구 정리' 보조자다. 각 뉴스에 대해 딱 두 가지만 판단한다.\n"
    "이건 최종 판단이 아니다 — 재무·감사 위험의 실제 판단은 뒤에서 더 강한 모델이 한다.\n"
    "\n"
    "(1) relevant: 이 기사가 그 회사의 '재무제표·감사 위험'과 관련될 '가능성'이 있는가?\n"
    "    - 느슨하게 본다. 조금이라도 관련 가능성이 있거나 애매하면 true.\n"
    "    - 명백히 무관한 것만 false: 신제품 광고·연예/스포츠 협찬·단순 인사 동정·수상/시상·\n"
    "      사회공헌/봉사 같은, 재무제표나 감사와 이어질 실마리가 없는 기사.\n"
    "    - 고정된 단어 목록으로 켜고 끄지 마라. 기사를 이해해서 판단하라(원칙 1).\n"
    "    - 놓치는 것이 잘못 통과시키는 것보다 나쁘다 — 확신이 없으면 true.\n"
    "\n"
    "(2) event_id: '같은 하나의 발표/국면'을 다룬 기사끼리 같은 정수를 준다(같은 국면의 중복만 묶기).\n"
    "    - 병합 단위는 '같은 사안'이 아니라 '같은 사건의 같은 국면'이다. 하나의 사안(예: 담합)이\n"
    "      여러 국면(조사 착수 → 첫 재판 → 과징금 확정 → 손배소송 → 정책 검토)으로 이어질 때,\n"
    "      국면(진행 단계·쟁점·확정성)이 다르면 별개의 실제 사건이므로 '다른 정수'를 준다.\n"
    "      각 국면은 감사 함의가 다르다(예: '과징금 확정'은 충당부채 확정 — 감사상 가장 중대). 국면을\n"
    "      뭉쳐 하나로 접으면 그 국면이 뒤 판단(더 강한 모델)에 도달하지 못하고 사라진다(오병합=증발, 원칙 7).\n"
    "    - 같은 국면(같은 발표/처분/단계)을 여러 매체가 다르게 쓴 '중복'만 같은 정수로 묶는다.\n"
    "      표현·제목 구성·강조점·함께 언급된 다른 회사가 달라도, 가리키는 것이 같은 하나의 발표면\n"
    "      같은 정수다(같은 발표를 매체마다 다른 제목으로 쓴 것뿐이다).\n"
    "      예: '가상제조·다른회사, 27개 품목 평균 8% 인상'과 '어느 회사 이어 가상제조, 햇반 등\n"
    "      평균 8% 올려'는 제목이 달라도 같은 하나의 가격 인상 발표다 → 같은 event_id(중복).\n"
    "      반례: 같은 담합 사안이라도 '공정위 심의 착수'와 '과징금 500억 확정'은 진행 국면이 달라\n"
    "      별개 사건이다 → 다른 event_id(각 국면의 대표가 각각 뒤 판단으로 가야 한다). '검토/저울질'과\n"
    "      그 뒤의 '확정/발표'도 국면이 다르면 다른 정수다.\n"
    "    - 또한, 단어·표현이 겹쳐도 실제 사건(주체·장소·시점·쟁점)이 다르면 합치지 마라.\n"
    "      예: '부산공장 화재'와 '울산공장 화재'는 표현이 비슷해도 다른 장소의 다른 사건이다.\n"
    "    - 서로 다른 사건, 또는 서로 다른 국면은 반드시 다른 정수. 같은 발표인지 정말 불확실하면\n"
    "      다른 정수로 둔다(임의로 합치지 마라 — 합치면 하나가 사라진다). '표현이 달라서'는 안 합칠\n"
    "      이유가 아니다. '실제로 다른 발표·다른 국면이라서'가 안 합칠 이유다.\n"
    "    - 요지: 같은 국면의 매체 중복은 하나로 접고, 사안의 진전(국면)이 다르면 국면별 대표가 각각\n"
    "      통과한다. 고정 단계 목록으로 기계적으로 나누지 말고 '진전·쟁점·확정성이 다른가'를 이해로\n"
    "      판별하라(원칙 1).\n"
    "\n"
    "모든 입력 뉴스에 대해 하나도 빠짐없이 결과를 낸다(source_index 로 대응)."
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "screenings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_index": {"type": "integer"},
                    "relevant": {"type": "boolean"},
                    "event_id": {"type": "integer"},
                },
                "required": ["source_index", "relevant", "event_id"],
            },
        }
    },
    "required": ["screenings"],
}


@dataclass
class PrescreenResult:
    survivors: list[NewsItem] = field(default_factory=list)       # Opus 풀판단으로 보낼 대표(관련)
    folded: list[tuple] = field(default_factory=list)             # [(NewsItem, 접힘사유)] — 보존·접근가능
    counts: dict = field(default_factory=dict)                    # collected/judged/dup_folded/…
    truncated: list = field(default_factory=list)


class PrescreenConfigError(RuntimeError):
    """API 키 미설정 등. 임의 값 대체 없이 멈춘다(원칙 4)."""


def _payload(company: str, items: list[NewsItem]) -> str:
    lines = [f"회사명: {company}", "", "뉴스 목록:"]
    for it in items:
        lines.append(f"[번호 {it.source_index}] 제목: {it.title}")
        if it.snippet:
            lines.append(f"  스니펫: {it.snippet}")
    return "\n".join(lines)


def _cache_key(payload: str) -> str:
    h = hashlib.sha256()
    h.update(_MODEL.encode("utf-8"))
    h.update(b"\x00")
    h.update(_SYSTEM.encode("utf-8"))
    h.update(b"\x00")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def _screen(client, company: str, items: list[NewsItem], on_usage=None) -> dict:
    """items 한 묶음을 Haiku 로 이해 → {source_index: {relevant, event_id}}. 캐시(유효 JSON만)."""
    payload = _payload(company, items)
    cache_file = _CACHE_DIR / f"{_cache_key(payload)}.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        # 스트리밍(긴 출력·높은 max_tokens 시 SDK 요구). Haiku 4.5 는 effort 미지원 → format 만.
        # 프롬프트 캐싱: 고정 부분(_SYSTEM)에 cache_control 을 걸어 배치마다 반복 전송되는 입력을
        # 캐시 적중(0.1x)으로 절감한다. 단 Haiku 4.5 의 최소 캐시 프리픽스는 4096 토큰이라 _SYSTEM
        # 이 그보다 짧으면 조용히 캐시되지 않는다(무해 — cache_creation=0). 실제 적중은 usage 로 계측.
        with client.messages.stream(
            model=_MODEL,
            max_tokens=32000,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": payload}],
        ) as stream:
            msg = stream.get_final_message()
        if on_usage is not None:
            on_usage(_MODEL, msg.usage)
        text = next(b.text for b in msg.content if b.type == "text")
        data = json.loads(text)                     # 파싱 성공을 확인한 뒤에만 캐시(깨진 JSON 캐시 금지)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(text, encoding="utf-8")

    by_idx = {it.source_index for it in items}
    out: dict[int, dict] = {}
    for row in data.get("screenings", []):
        si = row.get("source_index")
        if si in by_idx:
            out[si] = {"relevant": bool(row.get("relevant", True)),
                       "event_id": row.get("event_id")}
    # 누락분 fail-open: 관련 참 + 고유 event(놓치지 않기 위함).
    nxt = 1_000_000
    for it in items:
        if it.source_index not in out:
            out[it.source_index] = {"relevant": True, "event_id": nxt}
            nxt += 1
    return out


def _richness(it: NewsItem) -> int:
    return len(it.title or "") + len(it.snippet or "")


def _pick_rep(members: list[NewsItem], scr: dict) -> NewsItem:
    """대표 선택: 관련 기사가 있으면 그 중에서(누락 방지), 가장 정보가 풍부한 것. 결정론적."""
    rel = [m for m in members if scr[m.source_index]["relevant"]]
    pool = rel or members
    return max(pool, key=lambda m: (_richness(m), -m.source_index))


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def prescreen(company: str, items: list[NewsItem], *, on_usage=None) -> PrescreenResult:
    """수집한 뉴스를 이해 기반으로 중복 묶고(대표 1개) 명백 무관만 접는다. Opus 로 보낼 대표
    목록(survivors)과 접힌 것들(folded, 접근가능)을 돌려준다. 결과는 캐시된다(재실행 무비용)."""
    result = PrescreenResult()
    if not items:
        result.counts = {"collected": 0, "judged": 0, "dup_folded": 0,
                         "irrelevant_folded": 0, "truncated": 0}
        return result

    work = items[:_MAX_ITEMS]
    if len(items) > _MAX_ITEMS:
        result.truncated = items[_MAX_ITEMS:]
        for it in result.truncated:
            result.folded.append((it, "간이 정리 보류(수집 상한 초과 — 판단 안 됨, 보존)"))

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise PrescreenConfigError(
            "ANTHROPIC_API_KEY 환경변수가 없습니다. 임의 값 대체 없이 중단합니다(원칙 4)."
        )
    # max_retries: 동시성 아래의 일시적 429는 SDK 내장 백오프로 재시도. httpx 커넥션 풀은
    # 스레드 안전이라 한 클라이언트를 스레드가 공유해도 된다.
    client = anthropic.Anthropic(max_retries=5)

    # on_usage 는 이제 여러 워커 스레드에서 호출될 수 있다. 비-스레드안전 싱크를 위해
    # Lock 으로 직렬화한다(없으면 감싸지 않음).
    safe_on_usage = None
    if on_usage is not None:
        _usage_lock = threading.Lock()

        def safe_on_usage(model, usage, _cb=on_usage, _lock=_usage_lock):
            with _lock:
                _cb(model, usage)

    dup_folded = 0
    irrelevant_folded = 0

    # ── 1패스: 배치별 관련성 + 배치 안 중복 군집 → 배치 대표(관련만 다음 단계로) ──
    # 결정론(병렬 == 순차) 논거: 각 배치의 _screen 은 자기 payload 에만 의존하고 캐시 키도
    # payload 로만 결정된다(배치 간 독립). 그래서 _screen 호출만 병렬화하고, 군집·fold
    # 후처리(clusters/dup_folded/batch_reps/folded append)는 원래 배치 순서대로 수행한다.
    # → survivors/folded/counts 가 순차 결과와 정확히 같다.
    batches = list(_chunks(work, _BATCH))
    batch_scrs: dict[int, dict] = {}
    if batches:
        with ThreadPoolExecutor(max_workers=min(_MAX_CONCURRENCY, len(batches))) as pool:
            futures = {
                pool.submit(_screen, client, company, batch, on_usage=safe_on_usage): bi
                for bi, batch in enumerate(batches)
            }
            # 실패한 배치는 조용히 건너뛰지 않고 예외 전파(원칙 4: 데이터 무손실).
            for fut, bi in futures.items():
                batch_scrs[bi] = fut.result()

    batch_reps: list[NewsItem] = []
    for bi, batch in enumerate(batches):       # 원래 배치 순서대로 후처리
        scr = batch_scrs[bi]
        clusters: dict = {}
        for it in batch:
            clusters.setdefault(scr[it.source_index]["event_id"], []).append(it)
        for members in clusters.values():
            rep = _pick_rep(members, scr)
            for m in members:
                if m is not rep:
                    dup_folded += 1
                    result.folded.append((m, "중복 · 대표 기사에 통합"))
            if scr[rep.source_index]["relevant"]:
                batch_reps.append(rep)
            else:
                irrelevant_folded += 1
                result.folded.append((rep, "간이 스크리닝 · 재무·감사 무관"))

    # ── 2패스: 배치를 가로지르는 같은 사건을 전역으로 한 번 더 묶는다(단일 호출 → 순차 유지) ──
    if len(batch_reps) > 1:
        scr2 = _screen(client, company, batch_reps, on_usage=safe_on_usage)
        gclusters: dict = {}
        for it in batch_reps:
            gclusters.setdefault(scr2[it.source_index]["event_id"], []).append(it)
        for members in gclusters.values():
            rep = _pick_rep(members, scr2)          # 모두 관련이므로 richness 로만 대표 결정
            for m in members:
                if m is not rep:
                    dup_folded += 1
                    result.folded.append((m, "중복 · 대표 기사에 통합"))
            result.survivors.append(rep)
    else:
        result.survivors = batch_reps

    result.counts = {
        "collected": len(items),
        "judged": len(result.survivors),                 # Opus 풀판단 대상(J = K)
        "after_dedup": len(items) - dup_folded,          # 중복 제거 후 남은 건수
        "dup_folded": dup_folded,
        "irrelevant_folded": irrelevant_folded,
        "truncated": len(result.truncated),
    }
    return result
