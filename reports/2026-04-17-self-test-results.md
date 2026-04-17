# Self-Test Results

## Date

- 2026-04-17

## Scope

- 실제 파일시스템 기반 E2E smoke 테스트 추가
- CLI `once` black-box 경로 검증
- 기존 전체 단위/통합 테스트 재검증

## Added Files

- `tests/e2e/helpers.py`
- `tests/e2e/test_cli_smoke.py`
- `reports/tdd-self-test.md`

## E2E Scenarios

- one-way 복사 및 삭제 전파
- bidirectional 신규 파일 동기화 및 재실행 noop
- bidirectional manual conflict 중단과 state 비전진
- bidirectional keep_both conflict artifact 생성
- bidirectional state mismatch fail-fast

## Commands

```bash
python -m unittest tests.e2e.test_cli_smoke -v
python -m unittest discover -s tests -v
python -m compileall src tests
```

## Result

- `tests.e2e.test_cli_smoke`: 5 tests, OK
- 전체 `unittest discover`: 42 tests, OK
- `compileall src tests`: OK

## Notes

- scheduler failure-path 테스트는 의도적으로 예외 로그를 남기지만 테스트 결과는 정상입니다.
- busy 파일 자체 lock 같은 플랫폼 의존 시나리오는 기존 mock 기반 테스트에서 계속 검증합니다.
