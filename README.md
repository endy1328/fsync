# fsync

`fsync`는 Python 3.12 기반의 단방향 파일 동기화 도구입니다. 현재 기준 버전은 `1.0.0`이며, 하나의 source 디렉토리를 하나 이상 target 디렉토리로 복사하고 주기적으로 변경 사항만 다시 반영하도록 설계되어 있습니다.

## 기능

- `source -> target` 단방향 동기화
- source 1개를 여러 target으로 복사
- polling 기반 주기 실행과 1회 실행 지원
- 변경 파일만 재복사
- job 단위 병렬 처리
- `copy_deleted = true`일 때 source 삭제를 target에도 반영
- source 파일이 사용 중이면 다음 주기에 재시도

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

[[jobs]]
name = "documents-backup"
source = "/data/source/documents"
targets = [
  "/backup/a/documents",
  "/backup/b/documents",
]
interval_seconds = 60
copy_deleted = false
```

설정 항목:

- `app.max_workers`: 동시에 실행할 최대 job 수
- `app.log_level`: Python 로그 레벨
- `jobs[].name`: 로그에 표시할 job 이름
- `jobs[].source`: 원본 디렉토리
- `jobs[].targets`: 동기화할 대상 디렉토리 목록
- `jobs[].interval_seconds`: `run` 명령의 polling 주기
- `jobs[].copy_deleted`: source에서 삭제된 파일을 target에서도 삭제할지 여부

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

- target과 동일한 파일은 다시 복사하지 않습니다
- target 하나에서 실패해도 다른 job은 계속 실행됩니다
- `once`는 모든 job 실행이 끝난 뒤 결과를 반환합니다
- busy source 판정은 파일 접근 오류 기반의 best-effort 방식입니다

## 테스트

```bash
python3 -m unittest discover -s tests -v
```
