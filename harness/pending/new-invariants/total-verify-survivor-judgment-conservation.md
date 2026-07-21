# 처방(발의): 대표(survivor) 수 == 판단(judgment) 수 — Opus 경계에서 뉴스가 증발하면 안 된다

- 발의: 총괄검증방 / 출시 전 전수검증 / 2026-07-18
- 우선순위: **높음(blocker 후보)** — 원칙 7(숨기지 않음·증발 0)·원칙 4(누락 금지)의 미방어 경계
- 상태: 미반영(개발방 시공 대상)

## 무엇을 발견했나 (실측)

독립 라이브 재실행에서 **뉴스 1건이 화면 어디에도 없이 증발**했다.

- 태영건설 라이브(2026-07-18): 수집 1057 → 깔때기 후 대표(survivor) **206** → Opus 판단 결과(judgment) **205**.
  - prescreen 정합은 성립: 1057 = survivors 206 + 중복접힘 643 + 간이무관 208 (= 1057). **깔때기는 아무것도 안 잃음.**
  - 그러나 **survivors 206 → judgments 205**. Opus 배치가 입력 15건 중 14건만 돌려준 청크가 있었고, 코드는 그걸 검출하지 못했다.
  - 결과: 그 1건은 표시(51)·판단후접힘(154)·깔때기접힘(851) **어디에도 없다**. 화면 접근가능 총합 = 51+154+851 = 1056 ≠ 수집 1057.
  - 중복 source_index 아님(고유 205개 확인) — 진짜 1건 누락.
- 현대자동차 라이브(같은 날): 수집 1281 = 판단 66 + 접힘 1215 → **정합 OK(증발 0)**.

즉 **간헐적**이다. 과거 루프6b·루프7의 태영 실행은 "정합 OK(증발 0)"로 기록됐는데(changelog), 그 실행들은 우연히 Opus가 전건을 돌려줘 항등식이 성립했을 뿐이다. 델타검증이 매번 통과시킨 이유가 이 간헐성이다.

## 왜 기존 불변식이 못 잡았나

- `tests/invariants/test_prescreen.py::test_prescreen_conserves_all` 은 **prescreen 수준**(survivors+folded==collected)만 본다. 이는 항상 성립한다(깔때기는 보존).
- 누락은 그 **다음 경계**(engine.judge_items ↔ Opus)에서 난다. `judge_items`는 Opus가 돌려준 행 수를 입력 survivor 수와 대조하지 않는다(`parse_batch`가 돌려온 행마다 1 Judgment 생성, 누락 검출 없음). funnel의 `judged`는 `len(judgments)`라, 증발이 나면 조용히 `collected = judged + folded` 항등식만 1 어긋난다.
- 참고: 검증방 하네스 `tests/invariants/_harness.py::judge_cases` 는 `assert not missing`(모든 입력에 판단 존재)으로 이미 이 성질을 **합성 케이스에서** 단언한다. 그러나 **제품 경로(judge_items→pipeline.run)에는 같은 방어가 없다.**

## 처방 (개발방)

1. **불변식(검증방 소유):** `tests/invariants/` 에 'judge_items 는 입력 items 의 모든 source_index 에 대해 정확히 1건의 Judgment 를 반환한다(누락 0·중복 0)'를 세운다. 가능하면 API 없이 — `_judge_chunk` 가 부르는 클라이언트를 몽키패치해 '일부 행을 빠뜨린 응답'을 주입하고, judge_items 가 이를 검출(예외 또는 강제기권 보충)하는지 본다.
2. **제품 방어선(개발방 시공):** judge_items(또는 _judge_chunk) 가 반환 직전, 입력 survivor 의 source_index 집합과 반환 judgment 의 source_index 집합을 대조한다. 누락분은 **삭제하지 말고**(원칙 7) 강제기권 Judgment(예: `abstained=True`, `abstain_reason="판단 응답 누락 — 모델이 이 건을 돌려주지 않음, 사람 확인/재시도 필요"`, 근거는 지어내지 않음)로 채워 화면에 남긴다. 중복 source_index 반환도 검출해 정직하게 표기.
3. **정합 표기(선택):** funnel 에 `judged(=len(judgments))` 와 `survivors(=prescreen judged)` 를 함께 노출해, 둘이 다르면 화면·로그에서 드러나게 한다(블랙박스 금지, 원칙 7).

## 하지 말 것
- 누락분을 조용히 버리기(현재 상태 — 원칙 7 위반).
- 근거를 지어내 누락분을 '정상 판단'처럼 채우기(원칙 3 위반 — 강제기권으로 정직 표기).
- 재시도를 무한/무제한으로(비용·시간). 1~2회 재시도 후에도 누락이면 강제기권으로 남긴다.

_비개발자 요약:_ 뉴스를 넓게 모아 정밀 판단(Opus)에 보낼 때, 아주 가끔 판단 AI가 묶음 안의 한 건을 답에서 빠뜨리는데, 지금 코드는 그걸 알아채지 못해 그 한 건이 화면에서 완전히 사라집니다(이 도구의 제1 약속인 '어떤 뉴스도 숨기지 않는다'와 정면 충돌). 자주는 아니지만(같은 날 현대차는 멀쩡, 태영은 1건 증발) '증발 0'을 보장하려면, 보낸 건수와 돌아온 건수를 맞춰보고 빠진 건은 '판단 누락 — 사람 확인 필요'로 화면에 남기도록 고쳐야 합니다.

---
## 개발 반영 (루프9 — 개발방): 제품 방어선(#2)·정합 표기(#3) 시공. #1 불변식은 검증방 몫으로 잔류.
처방 #2·#3 을 반영했다:
- **#2 제품 방어선(engine.py):** `judge_items` 가 반환 직전 `_reconcile_returns(items, results)` 로
  입력 대표(survivor)의 source_index 집합과 반환 판단을 대조한다. 누락은 삭제하지 않고
  `schema.make_missing_judgment`(abstained=True·unjudged=True·근거/계정 지어냄 없음)로 보존해
  화면·집계에 '⚠ 미판단'으로 남긴다(원칙 7·4·3). 중복 source_index 는 첫 것만, 유령(입력에 없는
  번호)은 채택 안 함. 출력은 입력 순서로 재조립(결정론 — 병렬/순차·재실행 동일). 불일치는
  stderr 경고로도 알린다(조용히 넘어가지 않음). **판단(7축)·프롬프트·캐시·payload 무변경** —
  반환 집합 보존만 강제. 청크 '예외'는 여전히 그대로 전파(부분 반환 아님, test_parallel_invariance
  통과 유지) — 백필은 '성공했으나 일부 누락'일 때만.
- **#3 정합 표기(pipeline/server/serialize/UI):** funnel 에 `survivors`(대표 수)·`judged`(반환 수, 이제
  survivors 와 항상 동일)·`unjudged`(미판단 보존 수) 노출. counts 에 `unjudged` 추가. 미판단 항목은
  fold_reason='⚠ 미판단…' 로 접힘 뷰에 보존, 상태 뱃지 '⚠ 미판단', 깔때기 줄에 '⚠ 미판단 K건' 표기.
  정합 항등식 `수집 = 표시 + 접힘`(그 접힘 안에 미판단 K 포함)이 항상 성립.
- 실측(무API 몽키패치): 5건 중 4건 반환→누락 1건이 unjudged 로 보존(증발 0), 중복·유령 정리,
  서버 응답에서 수집7=표시4+접힘3·survivors5==judged5 확인. tests/unit/test_reconcile_no_evaporation.py
  3건(개발방 자체 테스트) 추가. 전체 unit 26 pass, test_parallel_invariance 2 pass(불변 유지).

**검증방 몫(원칙 10 — 개발방은 불변식을 못 만든다):** 처방 #1 의 `tests/invariants/` 불변식
('judge_items 는 입력 items 의 모든 source_index 에 정확히 1건 반환, 누락 0·중복 0'을 몽키패치로
독립 검증)은 검증방이 세운다. 이 파일은 그 불변식 저작 지시로 남긴다(개발방 unit 테스트는 검증
기준이 아니다). 검증 후 이 항목을 비운다.

---
## 검증방 완료 (루프9 검증 — 2026-07-18): 처방 #1 시공·red→green 실증. blocker 해소.
검증방이 `tests/invariants/test_survivor_judgment_conservation.py`(검증방 소유, 무-API 몽키패치)를
세웠다 — 개발방 unit 테스트는 신뢰하지 않고 독립 저작. 못박은 성질:
- 반환 source_index 집합 == 입력 집합(누락 0·중복 0·유령 0) — 뺄셈이 아닌 독립 집합 대조.
  부분반환·전멸청크·중복·유령을 각각 주입해도 정확히 1:1.
- 누락분은 미판단(unjudged·abstained)으로 보존되되 근거·계정·규모·심각도를 지어내지 않음(원칙 3).
- 미판단 비오염: 심각도 정렬에서 실제 판단 위로 못 올라옴(rank 0, 맨 아래), 계정 라우팅에서
  담당자 큐(①)로 안 새고 항상 ②미배정 공용 큐에 남음(원칙 7).
- **red→green 실증:** _reconcile_returns 를 항등으로 되돌려(=수정 전 반환 경로 정확 재현) 이 불변식
  파일을 돌리면 6건 중 5건 빨강(부분반환 시 idx 증발), 수정 후(reconcile 작동)엔 6/6 초록.
  ('전건 반환' 케이스만 pre/post 동일해 빨강 아님 — 잡을 증발이 없을 때만 통과, 정상.)
- 회귀: test_parallel_invariance 2건(키 있는 환경 재실행) 통과 — 병렬==순차 바이트 동일(reconcile
  가 입력순 재조립이라 병렬 불변 안 깸), 청크 실패 예외 전파 유지. 전체 스위트 60 pass /
  1 fail(=test_account_link_grounding, API 크레딧 소진으로 라이브콜 400 — 코드 회귀 아님·reconcile 무관).
- 서버 응답층 정합 실측: 수집 = 표시 + 접힘, 미판단 K 는 접힘의 '부분집합'(별도 가산 아님 —
  '수집=표시+접힘+미판단'으로 셋을 따로 더하면 K 만큼 이중계산). survivors==judged 독립 카운트 성립.

**출시 관문:** 증발 방어 blocker 해소로 판정. 처방 #1 시공 완료 — 이 항목 종료(검증방).
