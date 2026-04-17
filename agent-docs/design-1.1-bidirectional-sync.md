# fsync 1.1 양방향 동기화 설계

## 목표

- `fsync` 1.0의 단방향 sync 기능을 유지합니다.
- 설정에 따라 단방향 또는 양방향 job을 선택할 수 있게 합니다.
- 1.1에서는 2개 디렉토리 간 안전한 양방향 파일 sync만 지원합니다.
- 데이터 유실 위험을 줄이기 위해 충돌 감지와 상태 저장을 포함합니다.

## 비목표

- 3개 이상 peer 간 mesh sync
- 자동 내용 병합
- rename 추적
- symlink 전용 정책
- 디렉토리 외 특수 파일 지원

## 기본 방향

- 기존 단방향 엔진은 유지합니다.
- 양방향은 별도 엔진과 planner를 추가합니다.
- 설정 스키마는 one-way와 bidirectional을 모두 표현할 수 있어야 합니다.
- 양방향 삭제 전파는 상태 저장소 없이 허용하지 않습니다.

## 설정 모델

권장 형식:

```toml
[app]
max_workers = 4
log_level = "INFO"
state_dir = ".fsync-state"

[[jobs]]
name = "documents-one-way"
mode = "one_way"
source = "/data/source/documents"
targets = [
  "/backup/a/documents",
  "/backup/b/documents",
]
interval_seconds = 60
copy_deleted = false

[[jobs]]
name = "documents-bidirectional"
mode = "bidirectional"
left = "/data/a/documents"
right = "/data/b/documents"
interval_seconds = 60
delete_policy = "tracked"
conflict_policy = "keep_both"
initial_sync = "manual"
```

1.1 기본값:

- `mode = "one_way"` 또는 `mode = "bidirectional"`
- bidirectional state 포맷: JSON
- `delete_policy = "tracked"`
- `conflict_policy = "keep_both"`
- `initial_sync = "manual"`

## 내부 모델

- `mode = "one_way"`: 기존 1.0 모델 유지
- `mode = "bidirectional"`: `left`, `right`를 동등 peer로 취급
- `state_dir`: 양방향 기준 상태 파일 저장 위치

권장 타입:

- `OneWayJobConfig`
- `BidirectionalJobConfig`
- `BaselineEntry`
- `PlannedAction`
- `ConflictEntry`

권장 dataclass 구조:

```python
@dataclass(slots=True, frozen=True)
class OneWayJobConfig:
    name: str
    mode: Literal["one_way"]
    source: Path
    targets: tuple[Path, ...]
    interval_seconds: int = 60
    copy_deleted: bool = False


@dataclass(slots=True, frozen=True)
class BidirectionalJobConfig:
    name: str
    mode: Literal["bidirectional"]
    left: Path
    right: Path
    interval_seconds: int = 60
    delete_policy: Literal["tracked"] = "tracked"
    conflict_policy: Literal["keep_both", "manual", "newer_wins"] = "keep_both"
    initial_sync: Literal["manual", "left_wins", "right_wins"] = "manual"
    state_file: Path | None = None
```

`AppConfig.jobs`는 1.1부터 `tuple[OneWayJobConfig | BidirectionalJobConfig, ...]` 형태를 권장합니다.

## 설정 로드 규칙

- `mode`가 없으면 기존 설정과의 호환을 위해 `one_way`로 간주합니다.
- `mode = "one_way"`일 때는 `source`, `targets`가 필수입니다.
- `mode = "bidirectional"`일 때는 `left`, `right`가 필수입니다.
- `mode = "bidirectional"`일 때 `left == right`는 허용하지 않습니다.
- `mode = "bidirectional"`일 때 `state_file`이 없으면 `state_dir/<job-name>.json`을 기본값으로 사용합니다.
- 같은 실제 경로가 여러 bidirectional job에 중복 참여하면 설정 오류로 처리하는 편이 안전합니다.

## 상태 저장소

양방향은 마지막 동기화 기준선을 반드시 저장해야 합니다.

필수 정보:

- `relative_path`
- 마지막 동기화 시점의 left fingerprint
- 마지막 동기화 시점의 right fingerprint
- 마지막 성공 sync 시각 또는 revision

1.1에서는 JSON 파일로 시작합니다. 다만 파일 잠금과 원자적 쓰기를 고려해야 합니다.

권장 JSON 구조:

```json
{
  "version": 1,
  "job_name": "documents-bidirectional",
  "left_root": "/data/a/documents",
  "right_root": "/data/b/documents",
  "last_synced_at": "2026-04-17T15:00:00+09:00",
  "entries": {
    "reports/summary.txt": {
      "left": {
        "exists": true,
        "size": 1204,
        "mtime_ns": 1763355000000000000,
        "sha256": "..."
      },
      "right": {
        "exists": true,
        "size": 1204,
        "mtime_ns": 1763355000000000000,
        "sha256": "..."
      }
    }
  }
}
```

권장 상태 타입:

```python
@dataclass(slots=True, frozen=True)
class FileFingerprint:
    exists: bool
    size: int | None
    mtime_ns: int | None
    sha256: str | None


@dataclass(slots=True, frozen=True)
class BaselineEntry:
    left: FileFingerprint
    right: FileFingerprint


@dataclass(slots=True, frozen=True)
class BidirectionalState:
    version: int
    job_name: str
    left_root: str
    right_root: str
    last_synced_at: str | None
    entries: dict[str, BaselineEntry]
```

저장 규칙:

- state는 임시 파일에 쓴 뒤 atomic rename으로 교체합니다.
- 실행 중 오류가 있으면 state를 갱신하지 않습니다.
- job 이름이 바뀌어도 경로 기준이 같으면 명시적 migration 없이 새 state 파일로 시작합니다.

## 동기화 단계

1. left와 right를 각각 스캔합니다.
2. 상태 저장소를 읽습니다.
3. 경로별 diff를 계산합니다.
4. 실행 계획을 생성합니다.
5. 충돌 또는 위험한 액션을 검증합니다.
6. 복사와 삭제를 수행합니다.
7. 모든 작업이 성공하면 state를 커밋합니다.

planner와 executor는 분리합니다.

- planner: scan 결과와 baseline을 받아 액션 목록 생성
- executor: 액션 목록 실행, 성공 시 state 저장
- validator: 충돌 정책과 초기 sync 정책 위반 여부 확인

## 변경 판정

경로별로 아래 상태를 판정합니다.

- unchanged
- left_only_created
- right_only_created
- left_changed
- right_changed
- left_deleted
- right_deleted
- both_changed
- delete_modify_conflict

파일이 같은지 판단할 때는 size, mtime_ns, 필요 시 hash를 사용합니다.

권장 판정 순서:

1. left와 right 현재 fingerprint 수집
2. baseline entry 조회
3. 현재 값이 baseline 대비 어느 쪽에서 변했는지 계산
4. create, modify, delete, conflict, noop으로 정규화

예시:

- baseline 없음 + left만 존재: `left_only_created`
- baseline 있음 + left만 baseline과 다름: `left_changed`
- baseline 있음 + left 삭제 + right unchanged: `left_deleted`
- baseline 있음 + left 변경 + right 변경: `both_changed`
- baseline 있음 + left 삭제 + right 변경: `delete_modify_conflict`

## Planner 모델

권장 타입:

```python
@dataclass(slots=True, frozen=True)
class PlannedAction:
    kind: Literal[
        "copy_left_to_right",
        "copy_right_to_left",
        "delete_left",
        "delete_right",
        "write_conflict_left",
        "write_conflict_right",
        "noop",
    ]
    relative_path: str
    reason: str


@dataclass(slots=True, frozen=True)
class ConflictEntry:
    relative_path: str
    left_status: str
    right_status: str
    resolution: Literal["keep_both", "manual", "newer_wins"]
```

권장 planner 결과:

```python
@dataclass(slots=True, frozen=True)
class BidirectionalPlan:
    actions: tuple[PlannedAction, ...]
    conflicts: tuple[ConflictEntry, ...]
    deferred_paths: tuple[str, ...]
```

실행 규칙:

- `manual` 충돌 정책일 때 `conflicts`가 비어 있지 않으면 실행하지 않습니다.
- `keep_both`일 때 conflict 파일명은 원본 파일명과 분리되어야 합니다.
- `newer_wins`는 1.1에서 지원하더라도 기본값으로 사용하지 않습니다.

## 충돌 정책

1.1 기본값은 `keep_both`로 확정합니다.

충돌 사례:

- 양쪽 모두 수정
- 한쪽 수정, 다른 쪽 삭제
- 양쪽 모두 같은 경로에 신규 파일 생성, 내용 다름

정책 후보:

- `keep_both`: 한쪽 결과를 유지하고 다른 쪽은 conflict 이름으로 보존
- `manual`: 충돌 발생 시 적용 중단
- `newer_wins`: mtime 기준 승자 선택

안전성을 우선해 1.1에서는 `keep_both`를 기본 정책으로 사용합니다.

`keep_both` 파일명 규칙 예시:

- `report.txt` 충돌 시 `report.conflict-left-20260417T150000.txt`
- 타임스탬프는 로컬 시간대 대신 UTC 기반 포맷으로 고정하는 편이 안전합니다.

## 삭제 정책

1.1 기본값은 `tracked`로 확정합니다.

규칙:

- state에 존재하던 파일이 한쪽에서 사라졌고 반대편이 state와 동일할 때만 삭제 전파
- state 없이 한쪽에 없다는 사실만으로 삭제 전파하지 않음
- 초기 sync에서의 불일치는 삭제가 아니라 초기 차이로 취급

예시:

- baseline에 좌우 모두 있던 파일이 있고, 현재 left만 삭제되었고 right는 baseline과 동일: `delete_right`가 아니라 `delete_left` 의도 여부를 계산해 right 삭제 전파
- baseline에 없던 파일이 한쪽에만 존재: 삭제가 아니라 생성으로 처리
- baseline에 있던 파일이 left에서 삭제되고 right에서도 변경됨: 충돌

## 초기 sync 정책

정책 후보:

- `manual`
- `left_wins`
- `right_wins`

1.1 기본값은 `manual`로 확정합니다. 초기 차이가 있는 상태에서 자동 정렬을 기본으로 두면 데이터 유실 가능성이 커집니다.

초기 sync 규칙:

- baseline이 없고 동일 경로가 양쪽에 없으면 생성 전파
- baseline이 없고 동일 경로가 양쪽에 있으며 내용이 같으면 baseline만 생성
- baseline이 없고 동일 경로가 양쪽에 있으며 내용이 다르면 `manual`에서는 충돌로 보고 중단
- `left_wins` 또는 `right_wins`는 사용자가 명시적으로 선택한 경우에만 자동 정렬

## 권장 모듈 구조

- `src/fsync/config.py`: job schema 확장
- `src/fsync/syncer.py`: 기존 단방향 유지
- `src/fsync/bidir.py`: 양방향 실행 엔진
- `src/fsync/planner.py`: diff와 action plan 생성
- `src/fsync/state.py`: baseline 저장/로드

## 테스트 전략

- 단방향 기존 테스트 유지
- 양방향 신규 생성 전파
- 양방향 단일 수정 전파
- 양방향 동시 수정 충돌
- 양방향 삭제 전파
- 삭제와 수정 충돌
- state 저장 후 noop 재실행
- busy 파일 보류
- 실패 시 state 미커밋

## Agent 배분

- 메인 에이전트: 전체 방향, 설계 확정, 통합
- 계획 에이전트: 단계, 의존성, 완료 조건 정리
- 판단 에이전트: 충돌/삭제/초기 sync 정책 검토
- 실행 에이전트 A: 설정과 문서 변경
- 실행 에이전트 B: state, planner, 양방향 엔진 구현
- 실행 에이전트 C: 테스트와 회귀 검증
- 검증 에이전트: 데이터 유실 위험, 정책 누락, 테스트 공백 검토

## 브랜치 전략

- 시작 브랜치: `feature/bidirectional-sync-1.1`
- 필요 시 하위 브랜치:
  - `feature/bidir-config-state`
  - `feature/bidir-planner-engine`
  - `feature/bidir-tests-docs`

## 완료 조건

- 기존 단방향 1.0 동작이 유지됩니다
- 양방향 2-peer sync 설계가 코드 구조와 설정 모델에 반영됩니다
- 충돌, 삭제, 초기 sync 정책이 명시적으로 문서화됩니다
- 구현 단계와 검증 기준이 문서로 고정됩니다

## 이번 단계 확정 사항

- 구현 브랜치: `feature/bidirectional-sync-1.1`
- state 저장 포맷: JSON
- 초기 sync 기본값: `manual`
- 충돌 기본값: `keep_both`
- 삭제 기본값: `tracked`
