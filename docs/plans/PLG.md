# PLG 구현 계획

> 리서치 문서: [`docs/researchs/PLG.md`](../researchs/PLG.md)
> 작성일: 2026-02-27
> 채택 방식: **파일 기반 로깅 → Grafana Alloy → Loki → Grafana**

---

## 현재 상태 (As-Is)

```
python-starter-kit/
├── src/
│   └── main.py               ← main() 함수만 존재, 로깅 없음
├── pyproject.toml            ← python-logging-loki 의존성 포함 (제거 필요)
├── docker-compose.yml        ← Loki 서비스만 존재
└── config/
    └── loki/
        └── local-config.yaml ← Loki 설정만 존재
```

## 목표 상태 (To-Be)

```
python-starter-kit/
├── src/
│   ├── monitoring/
│   │   ├── __init__.py
│   │   └── logging.py        ← [신규] structlog + RotatingFileHandler
│   └── main.py               ← [수정] setup_logging() 호출
├── pyproject.toml            ← [수정] python-logging-loki 제거
├── docker-compose.yml        ← [수정] alloy + grafana 서비스 추가
└── config/
    ├── loki/
    │   └── local-config.yaml ← [기존 유지]
    ├── alloy/
    │   └── config.alloy      ← [신규] 로그 파일 tail → Loki 전송
    └── grafana/
        └── provisioning/
            └── datasources/
                └── loki.yaml ← [신규] Loki 데이터소스 자동 연결
```

---

## 구현 단계

### Step 1. 의존성 정리 (`pyproject.toml`)

**목적**: 파일 기반 방식에서 불필요한 `python-logging-loki` 제거

```toml
# 제거
- "python-logging-loki>=0.3.1"  # Direct Push 방식에서만 필요, 파일 방식에서는 불필요

# 유지
"structlog>=25.5.0"             # 구조화 로그 생성 (JSON 포맷)
```

---

### Step 2. 로깅 모듈 구현 (`src/monitoring/logging.py`)

**목적**: dev/prod 모두 파일(JSON)로 수집하되, dev는 콘솔 출력도 병행

#### 설계 원칙

| | dev | prod |
|-|-----|------|
| 파일 기록 (JSON) | ✅ Alloy가 수집 | ✅ Alloy가 수집 |
| 콘솔 출력 (Pretty) | ✅ 개발 편의 | ❌ |
| `env` 레이블 | `development` | `production` |
| Alloy 수집 | ✅ | ✅ |
| Grafana 모니터링 | 탐색적 (알림 없음) | 대시보드 + 알림 |

- `structlog.stdlib.ProcessorFormatter`로 핸들러별 다른 포맷 적용
  - 파일 핸들러 → `JSONRenderer` (Alloy 파싱용)
  - 콘솔 핸들러 → `ConsoleRenderer` (dev 전용)
- 모든 로그에 `env` 필드 포함 → Alloy가 Loki 레이블로 추출
- `ENV` 환경변수로 환경 구분 (`development` | `production`)

#### `src/monitoring/__init__.py`
```python
# 비워둠
```

#### `src/monitoring/logging.py`
```python
import logging
import logging.handlers
import os
from pathlib import Path

import structlog


def setup_logging(
    app_name: str = "python-starter-kit",
    log_dir: str = "/var/log/app",
    log_level: str = "INFO",
) -> None:
    env = os.getenv("ENV", "development")
    level = getattr(logging, log_level.upper(), logging.INFO)

    # 공통 프로세서: 모든 핸들러의 사전 처리 체인
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _add_env(env),   # 모든 로그에 env 필드 추가 → Alloy 레이블로 추출
    ]

    # structlog → stdlib logging 위임 (핸들러별 포맷 분기를 위해)
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 파일 핸들러 (JSON) — dev/prod 공통
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path / f"{app_name}.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(json_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)

    # 콘솔 핸들러 (Pretty) — dev 전용
    if env != "production":
        console_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(),
            ],
            foreign_pre_chain=shared_processors,
        )
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)


def _add_env(env: str):
    """모든 로그 이벤트에 env 필드를 추가하는 프로세서"""
    def processor(logger, method, event_dict):
        event_dict["env"] = env
        return event_dict
    return processor
```

#### 출력 예시

**dev — 콘솔 (개발자용)**
```
2026-02-27T12:00:00Z [info     ] 서버 시작                env=development port=8080
2026-02-27T12:00:01Z [error    ] DB 연결 실패             env=development host=localhost
```

**dev/prod 공통 — 파일 (Alloy 수집용)**
```json
{"timestamp":"2026-02-27T12:00:00Z","level":"info","event":"서버 시작","env":"development","port":8080}
{"timestamp":"2026-02-27T12:00:01Z","level":"error","event":"DB 연결 실패","env":"production","host":"localhost"}
```

---

### Step 3. 진입점 수정 (`src/main.py`)

**목적**: 앱 시작 시 로깅 설정 초기화

```python
import structlog

from src.monitoring.logging import setup_logging


def main() -> None:
    setup_logging()
    logger = structlog.get_logger()
    logger.info("애플리케이션 시작")


if __name__ == "__main__":
    main()
```

---

### Step 4. Grafana Alloy 설정 (`config/alloy/config.alloy`)

**목적**: `/var/log/app/*.log` 파일을 tail하여 JSON 파싱 후 Loki로 전송

```alloy
// 1. 수집 대상 파일 경로
local.file_match "python_app_logs" {
  path_targets = [{"__path__" = "/var/log/app/*.log"}]
  sync_period  = "5s"
}

// 2. 파일 tail
loki.source.file "python_app" {
  targets    = local.file_match.python_app_logs.targets
  forward_to = [loki.process.parse_json.receiver]
}

// 3. JSON 파싱 → 레이블 추출
loki.process "parse_json" {
  // level, service 필드를 Loki 레이블로 승격
  stage.json {
    expressions = {
      level   = "level",
      service = "service",
    }
  }

  stage.labels {
    values = {
      level   = "level",
      service = "service",
      env     = "env",     // dev/prod 자동 구분
    }
  }

  // 정적 레이블
  stage.static_labels {
    values = {
      app = "python-starter-kit",
    }
  }
  // env 레이블: JSON 필드에서 동적 추출 (dev/prod 자동 구분)
  // → Python 앱이 모든 로그에 "env" 필드를 포함하므로 별도 Alloy 설정 불필요

  forward_to = [loki.write.loki_server.receiver]
}

// 4. Loki 전송
loki.write "loki_server" {
  endpoint {
    url = "http://loki:3100/loki/api/v1/push"
  }
}
```

#### 레이블 설계

| 종류 | 레이블 키 | 값 예시 | 출처 |
|------|----------|--------|------|
| 정적 | `app` | `python-starter-kit` | Alloy 하드코딩 ✅ |
| 동적 | `env` | `development`, `production` | JSON `env` 필드 추출 ✅ |
| 동적 | `level` | `info`, `error` | JSON `level` 필드 추출 ✅ |
| 동적 | `service` | `auth`, `api` | JSON `service` 필드 추출 ✅ |
| 금지 | `user_id` | — | 높은 카디널리티 ❌ → JSON 필드 유지 |
| 금지 | `request_id` | — | 높은 카디널리티 ❌ → JSON 필드 유지 |

**Grafana 환경별 모니터링 분리 방법:**

```logql
# dev 로그만 조회
{app="python-starter-kit", env="development"}

# prod ERROR 알림 대상
{app="python-starter-kit", env="production", level="error"}
```

| 모니터링 항목 | dev | prod |
|-------------|-----|------|
| 로그 조회 | Explore 탐색 위주 | 대시보드 고정 |
| 알림(Alert) | 없음 | ERROR 발생 시 Slack 알림 |
| 보존 기간 | 단기 (7일) | 장기 (30일+) |

---

### Step 5. Grafana 데이터소스 자동 설정 (`config/grafana/provisioning/datasources/loki.yaml`)

**목적**: Grafana 시작 시 Loki 데이터소스 자동 등록 (수동 설정 불필요)

```yaml
apiVersion: 1

datasources:
  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
    isDefault: true
    editable: true
```

---

### Step 6. Docker Compose 업데이트 (`docker-compose.yml`)

**목적**: Alloy + Grafana 서비스 추가, `app-logs` 볼륨 공유

#### 추가할 서비스

**`alloy`**
```yaml
alloy:
  image: grafana/alloy:latest
  container_name: alloy
  ports:
    - "12345:12345"   # Alloy 관리 UI
  volumes:
    - ./config/alloy/config.alloy:/etc/alloy/config.alloy
    - app-logs:/var/log/app:ro    # Python 앱 로그 읽기 전용
  command: run /etc/alloy/config.alloy
  depends_on:
    loki:
      condition: service_healthy
  networks:
    - monitoring
```

**`grafana`**
```yaml
grafana:
  image: grafana/grafana:latest
  container_name: grafana
  ports:
    - "3000:3000"
  environment:
    - GF_SECURITY_ADMIN_USER=admin
    - GF_SECURITY_ADMIN_PASSWORD=admin
    - GF_USERS_ALLOW_SIGN_UP=false
  volumes:
    - grafana-data:/var/lib/grafana
    - ./config/grafana/provisioning:/etc/grafana/provisioning
  depends_on:
    loki:
      condition: service_healthy
  networks:
    - monitoring
```

#### 추가할 볼륨

```yaml
volumes:
  loki-data:
  grafana-data:
  app-logs:          # Python 앱(rw) ↔ Alloy(ro) 공유 볼륨
```

#### Python 앱 컨테이너 실행 시 볼륨 마운트 (참고)

```yaml
# Python 앱 컨테이너에 추가할 볼륨 설정 (앱 컨테이너화 시)
volumes:
  - app-logs:/var/log/app   # 읽기/쓰기
```

---

## 작업 순서 및 체크리스트

```
[ ] Step 1  pyproject.toml — python-logging-loki 제거 후 uv sync
[ ] Step 2  src/monitoring/__init__.py 생성
[ ] Step 2  src/monitoring/logging.py 구현
[ ] Step 3  src/main.py 수정
[ ] Step 4  config/alloy/config.alloy 생성
[ ] Step 5  config/grafana/provisioning/datasources/loki.yaml 생성
[ ] Step 6  docker-compose.yml — alloy, grafana 서비스 및 app-logs 볼륨 추가
[ ] 검증    docker compose up -d 실행 후 각 서비스 정상 동작 확인
[ ] 검증    Grafana (http://localhost:3000) 접속 → Loki 데이터소스 확인
[ ] 검증    Python 앱 실행 → 로그 파일 생성 → Grafana에서 로그 조회
```

---

## 검증 방법

### 로컬 파일 출력 확인

```bash
# dev: 콘솔 + 파일 동시 출력
ENV=development python -m src.main

# prod: 파일만 출력
ENV=production python -m src.main
cat /var/log/app/python-starter-kit.log
```

### Docker 환경 전체 검증

```bash
# 인프라 시작
docker compose up -d

# 각 서비스 상태 확인
docker compose ps

# Loki 준비 상태 확인
curl http://localhost:3100/ready

# Alloy UI (파이프라인 상태 확인)
open http://localhost:12345

# Grafana (admin/admin)
open http://localhost:3000
```

### LogQL로 환경별 조회 확인

```logql
# dev 로그 전체
{app="python-starter-kit", env="development"}

# prod ERROR만
{app="python-starter-kit", env="production", level="error"}
```

---

## 의존성 관계

```
Step 1 (의존성 정리)
  └─→ Step 2 (logging.py 구현)
        └─→ Step 3 (main.py 수정)

Step 4 (Alloy 설정)  ─┐
Step 5 (Grafana 설정) ─┤─→ Step 6 (docker-compose.yml 업데이트)
                        └─→ 검증
```
