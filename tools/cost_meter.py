"""
tools/cost_meter.py  (루프14 C-3 — 비용 계측 하네스, 판단 로직 아님)

파이프라인의 텔레메트리 훅 on_usage(model, usage) 를 받아 단계별 토큰·비용을 집계한다.
Haiku(입력/출력)·Opus(입력/출력)·캐시 적중분(생성/읽기)·총 USD 를 한 표로 뽑아, 프롬프트
캐싱·출력 다이어트의 최적화 전후 비교를 가능케 한다.

원칙 정합: 이 모듈은 판단 입력·출력·로직에 전혀 영향을 주지 않는다(순수 관측). src/ 코어가 아니라
tools/ 에 두어 코어(판단)와 하네스(계측) 자산을 분리한다(원칙 9의 정신). reference/ 를 import 하지 않는다.

가격(2026년 기준, per 1M tokens — claude-api 스킬 표):
  claude-opus-4-8 : 입력 $5,  출력 $25   (캐시 쓰기 1.25x 입력=$6.25, 캐시 읽기 0.1x 입력=$0.50)
  claude-haiku-4-5: 입력 $1,  출력 $5    (캐시 쓰기 $1.25,           캐시 읽기 $0.10)
가격은 변할 수 있으므로 한곳(_PRICES)에 모아 둔다. 계측이 곧 '진실'은 아니고 단가는 명시적 가정이다.
"""

import threading

# per 1M tokens (USD). (input, output). 캐시 쓰기=1.25x input, 캐시 읽기=0.1x input 은 코드가 유도.
_PRICES = {
    "opus": (5.0, 25.0),
    "haiku": (1.0, 5.0),
    "sonnet": (3.0, 15.0),
}
_M = 1_000_000


def _tier(model: str) -> str:
    m = (model or "").lower()
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    return "opus"  # 미상 모델은 보수적으로 가장 비싼 티어로 계상(과소평가 방지)


class CostMeter:
    """on_usage 콜백으로 모델별 토큰을 누적하고 USD 로 환산한다. 스레드 안전(내부 Lock)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tier -> {"calls","in","out","cache_write","cache_read"}
        self._acc: dict[str, dict[str, int]] = {}

    def on_usage(self, model, usage) -> None:
        """anthropic usage 객체(또는 동형 dict)를 받아 누적한다. 판단에 영향 없음."""
        tier = _tier(model if isinstance(model, str) else getattr(model, "id", ""))

        def g(name: str) -> int:
            if isinstance(usage, dict):
                v = usage.get(name)
            else:
                v = getattr(usage, name, None)
            return int(v or 0)

        with self._lock:
            a = self._acc.setdefault(
                tier, {"calls": 0, "in": 0, "out": 0, "cache_write": 0, "cache_read": 0})
            a["calls"] += 1
            a["in"] += g("input_tokens")               # 캐시 안 된 순수 입력분
            a["out"] += g("output_tokens")
            a["cache_write"] += g("cache_creation_input_tokens")
            a["cache_read"] += g("cache_read_input_tokens")

    def _tier_cost(self, tier: str, a: dict[str, int]) -> float:
        in_rate, out_rate = _PRICES[tier]
        return (
            a["in"] * in_rate
            + a["out"] * out_rate
            + a["cache_write"] * (1.25 * in_rate)
            + a["cache_read"] * (0.10 * in_rate)
        ) / _M

    def total_usd(self) -> float:
        with self._lock:
            return sum(self._tier_cost(t, a) for t, a in self._acc.items())

    def report(self) -> str:
        """사람이 읽는 단계별 표. 최적화 전후 비교용."""
        with self._lock:
            if not self._acc:
                return "[비용 계측] 사용량 없음(호출 0 또는 전부 로컬 캐시 적중)."
            lines = ["[비용 계측] 단계별 토큰·비용(가정 단가, USD)"]
            lines.append(
                f"  {'모델':<7} {'호출':>4} {'입력':>10} {'출력':>10} "
                f"{'캐시쓰기':>9} {'캐시읽기':>9} {'USD':>9}")
            total = 0.0
            for tier in ("haiku", "opus", "sonnet"):
                if tier not in self._acc:
                    continue
                a = self._acc[tier]
                c = self._tier_cost(tier, a)
                total += c
                lines.append(
                    f"  {tier:<7} {a['calls']:>4} {a['in']:>10,} {a['out']:>10,} "
                    f"{a['cache_write']:>9,} {a['cache_read']:>9,} {c:>9.4f}")
            lines.append(f"  {'합계':<7} {'':>4} {'':>10} {'':>10} {'':>9} {'':>9} {total:>9.4f}")
            # 캐시 절감 가시화: 캐시 읽은 토큰을 '캐시 없었다면 순입력이었을 것'으로 환산한 절감액.
            saved = 0.0
            for tier, a in self._acc.items():
                in_rate, _ = _PRICES[tier]
                saved += a["cache_read"] * (in_rate - 0.10 * in_rate) / _M
            if saved > 0:
                lines.append(f"  (프롬프트 캐시 적중으로 절감 추정: ${saved:.4f})")
            return "\n".join(lines)
