# Reports

이 디렉토리는 운영 문서가 아니라 검증 산출물을 보관합니다.

## 포함 대상

- self-test 실행 결과
- smoke 테스트 결과
- benchmark 결과
- 일회성 검증 기록

## 제외 대상

- 운영 규칙
- 현재 상태
- 계획
- 검증 기준
- 작업 이력

위 문서는 `agent-docs/`에 둡니다.

## 현재 파일

- `tdd-self-test.md`: self-test/TDD 전략 문서
- `2026-04-17-self-test-results.md`: 최신 self-test 실행 결과

## 파일명 규칙

- 반복 실행 결과는 의미가 분명한 이름을 사용합니다.
- 시점이 중요한 결과는 날짜를 붙입니다.

예시:

- `2026-04-17-self-test-results.md`
- `scheduler-smoke-results.md`
- `benchmark-sync-large-tree.md`

## 운영 원칙

- 테스트 코드 자체는 `tests/` 아래에 둡니다.
- `reports/`에는 코드가 아니라 사람이 읽는 결과와 요약만 둡니다.
- self-test 결과는 날짜형 파일명으로 저장하되 최신 결과 1개만 유지합니다.
- 현재 기준 문서와 충돌하지 않도록 필요하면 `agent-docs/worklog.md`에 결과 생성 사실만 남깁니다.
