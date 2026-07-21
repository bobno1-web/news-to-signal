"""
src/api/ — 웹 표시 계층(로컬 Flask).

이 계층은 '판단하지 않는다'. 기존 코어(judge/ranking/sources/pipeline)를 호출만 하고,
그 결과를 화면에 어떻게 보여줄지(직렬화·라우팅 렌즈)만 담당한다. 판단 로직을 여기서
재구현하지 않는다. src/ 는 reference/ 를 import 하지 않는다(원칙 9).
"""
