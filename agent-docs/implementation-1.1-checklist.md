# fsync 1.1 구현 체크리스트

## 목표

- 1.0 단방향 sync 기능을 유지합니다.
- 설정에 따라 one-way 또는 bidirectional job을 선택할 수 있게 합니다.
- 1.1에서는 2-peer 양방향 sync를 안전하게 추가합니다.

## 진행 규칙

- 각 단계는 완료 시 체크합니다.
- 구현 중 정책이 바뀌면 관련 설계 문서와 함께 갱신합니다.
- state는 모든 작업이 성공했을 때만 커밋합니다.
- 단방향 회귀 테스트는 모든 단계에서 유지합니다.

## Phase 1. Config 확장

- [x] `src/fsync/config.py`에 `OneWayJobConfig` 도입
- [x] `src/fsync/config.py`에 `BidirectionalJobConfig` 도입
- [x] `AppConfig.jobs`를 one-way/bidirectional union으로 확장
- [x] `app.state_dir` 설정 추가
- [x] `mode` 미지정 시 `one_way` 기본 처리
- [x] `bidirectional` 필수 필드 검증 추가
- [x] 잘못된 설정 조합 검증 추가

완료 조건:

- 기존 one-way 설정이 그대로 로드됩니다.
- bidirectional 설정이 정상 로드됩니다.
- 잘못된 설정은 명확한 예외로 실패합니다.

## Phase 2. State 저장소

- [x] `src/fsync/state.py` 추가
- [x] `FileFingerprint`, `BaselineEntry`, `BidirectionalState` 정의
- [x] JSON 직렬화/역직렬화 구현
- [x] 기본 state 파일 경로 계산 구현
- [x] 임시 파일 + atomic rename 저장 구현
- [x] 오류 시 state 미커밋 보장

완료 조건:

- state 저장 후 재로딩 시 값이 유지됩니다.
- 오류 시 기존 state 파일이 손상되지 않습니다.

## Phase 3. Planner 구현

- [x] `src/fsync/planner.py` 추가
- [x] 현재 snapshot과 baseline diff 계산 구현
- [x] `PlannedAction`, `ConflictEntry`, `BidirectionalPlan` 정의
- [x] `manual` 초기 sync 정책 반영
- [x] `tracked` 삭제 정책 반영
- [x] `keep_both` 충돌 정책 반영

완료 조건:

- create, modify, delete, conflict, noop이 구분됩니다.
- baseline 없는 초기 상태를 처리합니다.

## Phase 4. Bidirectional Executor 구현

- [x] `src/fsync/bidir.py` 추가
- [x] planner 결과 실행 구현
- [x] left -> right 복사 구현
- [x] right -> left 복사 구현
- [x] 상태 기반 삭제 전파 구현
- [x] conflict 파일 생성 구현
- [x] busy 파일 보류 처리 구현
- [x] 성공 시에만 state 커밋 구현

완료 조건:

- bidirectional `once` 실행이 동작합니다.
- 실패 시 state가 전진하지 않습니다.

## Phase 5. Scheduler 연결

- [x] `src/fsync/scheduler.py`에서 job 타입별 엔진 분기
- [x] one-way는 기존 `SyncEngine` 유지
- [x] bidirectional은 새 엔진 연결
- [x] mixed job 구성에서 `once` 동작 확인

완료 조건:

- one-way 회귀가 없습니다.
- mixed job 구성이 동작합니다.

## Phase 6. 테스트

- [x] bidirectional config 로드 테스트 추가
- [x] left 신규 -> right 복사 테스트 추가
- [x] right 신규 -> left 복사 테스트 추가
- [x] 한쪽 수정 전파 테스트 추가
- [x] 양쪽 수정 충돌 테스트 추가
- [x] 삭제 전파 테스트 추가
- [x] 삭제 vs 수정 충돌 테스트 추가
- [x] state 저장 후 noop 테스트 추가
- [x] busy 파일 보류 테스트 추가
- [x] 실패 시 state 미커밋 테스트 추가
- [x] one-way 회귀 테스트 유지 확인

완료 조건:

- one-way와 bidirectional 핵심 시나리오가 자동 테스트를 통과합니다.

## Phase 7. 문서 및 예제 설정

- [x] `README.md`에 one-way / bidirectional 설명 추가
- [x] `config.example.toml`에 bidirectional 예제 추가
- [x] 정책과 제한사항 문서 반영

완료 조건:

- 사용자가 설정과 제한사항을 README만 보고 이해할 수 있습니다.

## 현재 진행 순서

1. Config 확장
2. State 저장소
3. Planner 구현
4. Bidirectional Executor 구현
5. Scheduler 연결
6. 테스트
7. 문서 및 예제 설정
