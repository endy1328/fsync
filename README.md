# fsync

`fsync`는 Python 3.12 기반 파일 동기화 도구입니다. 현재 기준 버전은 `1.1.0-dev`이며, 설정에 따라 기존 `one_way` 백업 동기화와 2-peer `bidirectional` 동기화를 함께 실행할 수 있습니다.

## 기능

- `source -> target` 단방향 동기화
- source 1개를 여러 target으로 복사
- left/right 2-peer 양방향 동기화
- polling 기반 주기 실행과 1회 실행 지원
- 변경 파일만 재복사
- job 단위 병렬 처리
- `copy_deleted = true`일 때 one-way source 삭제를 target에도 반영
- bidirectional `tracked` 삭제 전파
- `keep_both`, `manual`, `newer_wins` 충돌 정책
- 파일이 사용 중이면 해당 파일만 보류하고 다음 주기에 재시도

## 요구 사항

- Python 3.12 이상

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 설정

설정 파일은 TOML 형식이며 기본 경로는 `config.toml`입니다. 시작할 때 [`config.example.toml`](config.example.toml)를 복사해서 사용하면 됩니다.

```bash
cp config.example.toml config.toml
```

예시:

```toml
[app]
max_workers = 8
log_level = "INFO"
state_dir = ".fsync-state"

[[jobs]]
name = "documents-backup"
mode = "one_way"
source = "/data/source/documents"
targets = [
  "/backup/a/documents",
  "/backup/b/documents",
]
interval_seconds = 60
copy_deleted = false

[[jobs]]
name = "documents-sync"
mode = "bidirectional"
left = "/data/a/documents"
right = "/data/b/documents"
interval_seconds = 60
delete_policy = "tracked"
conflict_policy = "keep_both"
initial_sync = "manual"
```

### 공통 설정

- `app.max_workers`: 동시에 실행할 최대 job 수
- `app.log_level`: Python 로그 레벨
- `app.state_dir`: bidirectional state 기본 저장 디렉토리
- `jobs[].name`: 로그에 표시할 job 이름
- `jobs[].interval_seconds`: `run` 명령의 polling 주기
- `jobs[].mode`: `one_way` 또는 `bidirectional`, 생략 시 `one_way`

### one-way job 설정

- `jobs[].source`: 원본 디렉토리
- `jobs[].targets`: 동기화할 대상 디렉토리 목록
- `jobs[].copy_deleted`: source에서 삭제된 파일을 target에서도 삭제할지 여부

### bidirectional job 설정

- `jobs[].left`: 첫 번째 peer 디렉토리
- `jobs[].right`: 두 번째 peer 디렉토리
- `jobs[].state_file`: 개별 state 파일 경로, 생략 시 `app.state_dir/<job>.json`
- `jobs[].delete_policy`: 현재는 `tracked`만 지원
- `jobs[].conflict_policy`: `keep_both`, `manual`, `newer_wins`
- `jobs[].initial_sync`: `manual`, `left_wins`, `right_wins`

## 실행

1회 실행:

```bash
fsync --config config.toml once
```

계속 실행:

```bash
fsync --config config.toml run
```

인자를 생략하면 기본값은 `run`입니다.

## 동작 방식

- one-way job은 기존 1.0 동작을 유지합니다
- bidirectional job은 state 파일을 기준으로 create, modify, delete, conflict를 판정합니다
- state 파일의 job 이름이나 left/right root가 현재 설정과 다르면 즉시 중단합니다
- `manual` 충돌 정책은 충돌이 있으면 실행을 중단하고 state를 전진시키지 않습니다
- `keep_both`는 양쪽 원본을 유지하고 반대편 버전의 conflict 파일을 추가 생성합니다
- busy 파일은 해당 파일만 보류하고, 성공 파일만 다음 baseline에 반영합니다
- `once`는 모든 job 실행이 끝난 뒤 결과를 반환합니다

## 제한사항

- bidirectional은 2-peer 디렉토리 sync만 지원합니다
- rename 추적은 지원하지 않습니다
- symlink와 특수 파일 전용 정책은 아직 없습니다
- conflict artifact는 state baseline에 포함되지 않습니다

## 테스트

```bash
python3 -m unittest discover -s tests -v
```
