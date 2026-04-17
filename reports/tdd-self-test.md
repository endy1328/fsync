# TDD Self-Test Strategy

## 목적

- 실제 파일시스템 상에서 `fsync`가 의도대로 동작하는지 black-box에 가깝게 검증합니다.
- 고정 sandbox 디렉토리 대신 테스트마다 임시 디렉토리를 생성해 오염과 충돌을 막습니다.

## 원칙

- 테스트 코드는 `tests/` 아래에 둡니다.
- 실행 중에만 `TemporaryDirectory()`로 `source/target` 또는 `left/right/state`를 만듭니다.
- 검증은 파일 존재, 파일 내용, state 파일 내용에 집중합니다.
- 플랫폼 의존성이 큰 busy/lock 시나리오는 단위 테스트에서 mock으로 유지합니다.

## 계층

1. 단위 테스트
- planner/state/scheduler 보조 로직 검증

2. 통합 테스트
- 엔진 직접 호출로 copy/delete/conflict/state commit 검증

3. E2E smoke 테스트
- CLI `once` 경로를 통해 실제 설정 파일, 실제 디렉토리, 실제 파일을 사용해 검증

## 현재 E2E 범위

- one-way 복사 및 삭제 전파
- bidirectional 신규 파일 동기화
- bidirectional manual conflict 중단
- bidirectional keep_both conflict artifact 생성
- bidirectional state mismatch fail-fast
- bidirectional 재실행 noop

## 파일 위치

- `tests/e2e/helpers.py`
- `tests/e2e/test_cli_smoke.py`

## 실행 명령

```bash
python -m unittest tests.e2e.test_cli_smoke -v
python -m unittest discover -s tests -v
python -m compileall src tests
```
