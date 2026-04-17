# 작업 이력

## 2026-04-14

- 초기 `fsync` 구성을 위해 저장소 상태를 검토했습니다.
- 계획 서브에이전트를 통해 계획 방향을 정리했습니다.
- 문서 세트 구성을 위해 3개의 독립 판단 관점을 수집했습니다.
- `agent.md`, workflow, plan, status, verification 문서를 중심으로 첫 문서 기준선을 정리했습니다.
- 문서 기준선이 검증되기 전까지 추가 구현을 보류했습니다.
- 검증 피드백을 반영해 현재 단계 명칭을 `documentation-first`로 통일했습니다.
- `src/fsync/*`, `tests/*`의 기존 초안 구현을 폐기하지 않고 1.0 베이스라인으로 재사용했습니다.
- `TOML [[jobs]]`, polling-only 스케줄링, job 단위 병렬 sync 기준으로 1.0 동작을 정리했습니다.
- busy source 파일이 현재 주기에서 보류되고 다음 주기에 재시도되도록 구현했습니다.
- `copy_deleted`를 job 단위 옵션으로 유지하고 기본값을 `false`로 두었습니다.
- 성공적으로 동기화된 파일만 snapshot에 반영되도록 정책을 수정했습니다.
- target 쓰기/삭제 실패가 현재 job을 중단하고 재시도 의미를 유지하도록 수정했습니다.
- 보류 재시도와 혼합 성공/보류 snapshot 동작에 대한 자동 테스트를 추가했습니다.
- one-shot 실행에서 한 job 실패가 다른 job 완료를 막지 않도록 수정했습니다.
- target metadata 실패 처리와 one-shot 다중 job 실패 격리 테스트를 추가했습니다.
- 변경 여부 판정을 메모리 snapshot 기준에서 실제 target 파일 상태 비교 기준으로 변경했습니다.
- 재시작 후 재복사 방지와 일부 target만 누락된 경우의 부분 복구 테스트를 추가했습니다.
- README와 운영 문서를 현재 1.0 구현 상태에 맞게 갱신했습니다.

## 2026-04-17

- 1.1 개발 목표를 단방향 유지 + 양방향 sync 추가로 정의했습니다.
- 양방향을 기존 단방향 엔진 수정이 아니라 별도 엔진 추가 방식으로 설계했습니다.
- 설정 기반으로 one-way와 bidirectional job을 공존시키는 방향을 채택했습니다.
- 양방향 핵심 정책으로 상태 저장, 충돌 감지, 상태 기반 삭제 전파가 필요하다고 정리했습니다.
- `agent-docs/design-1.1-bidirectional-sync.md`에 1.1 설계 초안을 작성했습니다.
- 계획, 상태, Agent 배분 문서를 1.1 준비 단계 기준으로 갱신했습니다.
- `feature/bidirectional-sync-1.1` 브랜치를 생성했습니다.
- 1.1 기본 정책으로 JSON state, `manual` 초기 sync, `keep_both` 충돌, `tracked` 삭제를 확정했습니다.
- `src/fsync/config.py`를 one-way/bidirectional 공존 구조로 확장했습니다.
- `state_dir`, bidirectional validation, default state file 계산을 설정 로더에 추가했습니다.
- 설정 로드 및 회귀 검증을 위해 config 테스트 4개를 추가했고 전체 테스트를 통과했습니다.
- `src/fsync/state.py`에 JSON state 저장소와 atomic save를 구현했습니다.
- `src/fsync/planner.py`에 baseline diff와 planner action 모델을 구현했습니다.
- `src/fsync/bidir.py`에 bidirectional executor를 추가하고 keep_both conflict artifact 생성을 연결했습니다.
- conflict artifact를 planner snapshot에서 제외하도록 조정했습니다.
- `src/fsync/scheduler.py`에서 one-way와 bidirectional job을 엔진별로 분기하도록 확장했습니다.
- README, 예제 설정, 하네스 문서를 1.1 다중 에이전트 운영 방식에 맞춰 갱신했습니다.
- bidirectional executor 통합 테스트를 추가해 신규 복사, tracked 삭제, manual conflict, keep_both, busy defer, state 미커밋, noop 재실행을 검증했습니다.
- scheduler mixed job 테스트를 별도 파일로 추가해 one-way/bidirectional 엔진 분기와 실패 집계를 검증했습니다.
- 전체 `python -m unittest discover -s tests -v`와 `python -m compileall src tests`를 통과했습니다.
- bidirectional state 파일이 현재 job name/left/right root와 다르면 즉시 중단하도록 fail-fast 검증을 추가했습니다.
- scheduler run_forever 경로는 `_submit_if_due`와 `_complete` 단위 테스트로 mixed job 제출/재시도를 검증했습니다.
- `tests/e2e/test_cli_smoke.py`에 실제 임시 디렉토리 기반 CLI smoke 테스트를 추가했습니다.
- `reports/tdd-self-test.md`와 `reports/2026-04-17-self-test-results.md`에 TDD 전략과 실행 결과를 기록했습니다.
