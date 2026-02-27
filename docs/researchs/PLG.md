# PLG 스택 리서치: Python 로깅 모니터링 환경 구축

> **PLG = (P)romtail 후계자 Alloy + (L)oki + (G)rafana**
>
> 리서치 일자: 2026-02-27
> 적용 프로젝트: `python-starter-kit`
> 채택 방식: **파일 기반 로깅 → Grafana Alloy → Loki**

---

## 1. PLG 스택 개요 및 채택 아키텍처

### 1.1 최종 채택 아키텍처: 파일 기반 + Alloy 에이전트

```
[Python App]
    │  structlog (구조화 JSON 로그 생성)
    │  RotatingFileHandler (파일로 기록)
    ▼
[Log Files]  /var/log/app/app.log  (앱과 모니터링 시스템 완전 분리)
    │
    ▼
[Grafana Alloy]  ──── 파일 tail → 레이블 추가 → Loki 전송
    │
    ▼
[Loki]  ──── 로그 저장 및 인덱싱 (LogQL 지원)
    │
    ▼
[Grafana]  ──── 대시보드 시각화 / 알림
```

### 1.2 파일 기반 방식을 선택한 이유

| 항목 | 파일 + Alloy (채택) | Direct Push |
|------|-------------------|-------------|
| 앱 코드 의존성 | 표준 파일 핸들러만 사용 | Loki 라이브러리 필요 |
| Loki 다운 시 | 파일에 안전하게 보존, 복구 시 재전송 | **로그 유실 위험** |
| 앱 성능 영향 | 없음 (파일 I/O만) | HTTP 전송 오버헤드 |
| 모니터링 시스템 장애 전파 | 격리됨 (앱 영향 없음) | 앱에 직접 영향 가능 |
| 재전송 / 재시도 | Alloy가 자동 처리 | 직접 구현 필요 |
| 권장 대상 | **안정적인 서비스 운영** | 마이크로서비스/서버리스 |

> **핵심 원칙**: 애플리케이션은 로그를 파일에 쓰는 책임만 가진다.
> 수집·전송은 인프라(Alloy)가 담당한다.

### 1.3 각 구성 요소

| 구성 요소 | 역할 | 상태 |
|-----------|------|------|
| **Grafana Alloy** | 로그 파일을 읽어 Loki로 전송하는 에이전트 (Promtail 후계자) | ✅ **현재 표준** |
| ~~Promtail~~ | ~~Alloy의 전신~~ | ⚠️ Deprecated (2025.02 LTS→2026.03 EoL) |
| **Loki** | 로그 집계 및 저장소, LogQL 쿼리 지원 | ✅ |
| **Grafana** | Loki 연동 대시보드 및 알림 | ✅ |

---

## 2. Python 로깅 구현 (파일 기반)

### 2.1 핵심 라이브러리

```toml
# pyproject.toml 의존성
# Loki 관련 라이브러리 불필요 — 표준 파일 핸들러만 사용
structlog   # 구조화 로그 생성 (JSON 포맷)
```

### 2.2 structlog 설정 (환경별 분리)

```python
# src/core/logging.py
import logging
import logging.handlers
import structlog
import os
from pathlib import Path

def setup_logging(
    log_dir: str = "/var/log/app",
    app_name: str = "python-starter-kit",
    env: str = "development",
    log_level: str = "INFO",
) -> None:
    """
    로깅 설정.
    - 개발: 콘솔 출력 (가독성 우선)
    - 운영: JSON 파일 출력 (Alloy가 읽어 Loki로 전송)
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # 공통 프로세서 (모든 환경 공통)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),   # ISO 8601 UTC
        structlog.processors.StackInfoRenderer(),
    ]

    if env == "production":
        # --- 운영: JSON 파일 출력 ---
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        # 표준 logging: 파일 핸들러 (10MB × 5개 rotation)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / f"{app_name}.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)

        logging.basicConfig(
            level=level,
            handlers=[file_handler],
            format="%(message)s",  # structlog이 포맷 담당
        )

        structlog.configure(
            processors=shared_processors + [
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),   # JSON 한 줄씩 출력
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

    else:
        # --- 개발: 컬러 콘솔 출력 ---
        logging.basicConfig(level=level, format="%(message)s")

        structlog.configure(
            processors=shared_processors + [
                structlog.dev.ConsoleRenderer(),       # 가독성 높은 콘솔 출력
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
```

### 2.3 실사용 예시

```python
import structlog

logger = structlog.get_logger()

# 컨텍스트 바인딩 (요청 단위 추적 — request_id는 레이블 ❌, JSON 필드 ✅)
bound_logger = logger.bind(
    request_id="req-abc-123",
    user_id="user-456",
    service="auth",
)

bound_logger.info("로그인 시도", method="oauth2")
bound_logger.warning("비밀번호 틀림", attempt=3)
bound_logger.error("계정 잠김", locked_until="2026-02-28T00:00:00Z")
```

**운영 환경 파일 출력 (JSON 한 줄):**
```json
{"timestamp": "2026-02-27T12:00:00Z", "level": "error", "logger": "auth", "event": "계정 잠김", "request_id": "req-abc-123", "user_id": "user-456", "service": "auth", "locked_until": "2026-02-28T00:00:00Z"}
```

**개발 환경 콘솔 출력:**
```
2026-02-27T12:00:00Z [error    ] 계정 잠김  logger=auth request_id=req-abc-123 service=auth
```

---

## 3. Grafana Alloy 설정

### 3.1 Alloy란?

Grafana Alloy는 **OpenTelemetry Collector** 기반의 통합 관측성 에이전트입니다.
Promtail(로그만)과 달리 **로그 + 메트릭 + 트레이스**를 하나의 에이전트로 처리합니다.

```
Promtail (deprecated) ──→ Grafana Alloy (현재 표준)
   로그만 수집              로그 + 메트릭 + 트레이스 통합
```

### 3.2 Alloy 설정 파일 (`config/alloy/config.alloy`)

```alloy
// =============================================================
// Python 앱 로그 파일 수집 → Loki 전송
// =============================================================

// 1. 수집 대상 로그 파일 경로 정의
local.file_match "python_app_logs" {
  path_targets = [{
    "__path__" = "/var/log/app/*.log",
  }]
  sync_period = "5s"   // 5초마다 새 파일 확인
}

// 2. 파일 tail 및 레이블 부착
loki.source.file "python_app" {
  targets               = local.file_match.python_app_logs.targets
  forward_to            = [loki.process.add_labels.receiver]
}

// 3. 로그 파싱 및 레이블 추가
loki.process "add_labels" {
  // JSON 로그 파싱 → level 필드를 Loki 레이블로 추출
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
    }
  }

  // 정적 레이블 추가 (낮은 카디널리티)
  stage.static_labels {
    values = {
      app = "python-starter-kit",
      env = "production",
    }
  }

  forward_to = [loki.write.default.receiver]
}

// 4. Loki로 전송
loki.write "default" {
  endpoint {
    url = "http://loki:3100/loki/api/v1/push"
  }
}
```

### 3.3 Alloy 레이블 전략

Loki 핵심 원칙: **낮은 카디널리티 레이블**만 사용

```
✅ Alloy 레이블 (낮은 카디널리티)     ❌ 절대 레이블로 사용 금지
─────────────────────────────────    ──────────────────────────
app     = "python-starter-kit"       user_id    (사용자마다 다름)
env     = "production"               request_id (요청마다 다름)
level   = "error"                    ip_address (IP마다 다름)
service = "auth"
```

→ `user_id`, `request_id` 등은 **JSON 로그 메시지 필드**로 포함하고,
  Grafana에서 `| json | user_id="456"` 으로 필터링합니다.

---

## 4. 인프라 구성 (Docker Compose)

### 4.1 전체 서비스 구성

```
docker-compose.yml
├── loki      (포트 3100) — 로그 저장소
├── alloy     (포트 12345) — 로그 수집 에이전트
└── grafana   (포트 3000) — 대시보드
```

### 4.2 Loki 설정 (`config/loki/local-config.yaml`)

```yaml
auth_enabled: false

server:
  http_listen_port: 3100
  grpc_listen_port: 9096

common:
  instance_addr: 127.0.0.1
  path_prefix: /tmp/loki
  storage:
    filesystem:
      chunks_directory: /tmp/loki/chunks
      rules_directory: /tmp/loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

limits_config:
  reject_old_samples: true
  reject_old_samples_max_age: 168h  # 7일

query_range:
  results_cache:
    cache:
      embedded_cache:
        enabled: true
        max_size_mb: 100
```

### 4.3 Grafana 자동 데이터소스 (`config/grafana/provisioning/datasources/loki.yaml`)

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

### 4.4 전체 Docker Compose

```yaml
services:
  loki:
    image: grafana/loki:3.4.2
    container_name: loki
    ports:
      - "3100:3100"
    command: -config.file=/etc/loki/local-config.yaml
    volumes:
      - ./config/loki/local-config.yaml:/etc/loki/local-config.yaml
      - loki-data:/tmp/loki
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O /dev/null http://localhost:3100/ready || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 15s
    networks:
      - monitoring

  alloy:
    image: grafana/alloy:latest
    container_name: alloy
    ports:
      - "12345:12345"   # Alloy UI (모니터링)
    volumes:
      - ./config/alloy/config.alloy:/etc/alloy/config.alloy
      - app-logs:/var/log/app:ro    # Python 앱 로그 (읽기 전용)
    command: run /etc/alloy/config.alloy
    depends_on:
      loki:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:11.4.0
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
    restart: unless-stopped
    networks:
      - monitoring

networks:
  monitoring:
    driver: bridge

volumes:
  loki-data:
  grafana-data:
  app-logs:       # Python 앱과 Alloy가 공유하는 로그 볼륨
```

> **볼륨 공유 방식**: Python 앱 컨테이너와 Alloy 컨테이너가 `app-logs` 볼륨을 공유.
> 앱은 쓰고(`rw`), Alloy는 읽기만(`ro`) 합니다.

---

## 5. LogQL 쿼리 언어

### 5.1 기본 문법

```logql
{스트림_셀렉터} | 파이프라인
```

### 5.2 자주 사용하는 쿼리 패턴

```logql
# 1. 특정 앱의 모든 로그 조회
{app="python-starter-kit"}

# 2. ERROR 레벨 로그만 (Alloy가 level 레이블 추출한 경우)
{app="python-starter-kit", level="error"}

# 3. JSON 파싱 후 특정 필드 필터링 (user_id는 레이블이 아닌 JSON 필드)
{app="python-starter-kit"} | json | user_id="user-456"

# 4. 특정 서비스의 에러
{app="python-starter-kit", service="auth", level="error"}

# 5. 키워드 검색
{app="python-starter-kit"} |= "계정 잠김"

# 6. 정규식 필터
{app="python-starter-kit"} |~ "attempt=[3-9]"

# 7. 시간당 로그 발생 건수 (메트릭)
rate({app="python-starter-kit"}[5m])

# 8. 서비스별 에러 비율
sum(rate({app="python-starter-kit", level="error"}[5m])) by (service)
```

### 5.3 JSON 구조화 로그와 LogQL 활용

Alloy가 `level`, `service`를 레이블로 추출하면:

```
스트림 셀렉터만으로 빠른 필터링 → {app="...", level="error", service="auth"}
JSON 필드는 파이프라인에서 → | json | user_id="..." | request_id="..."
```

---

## 6. Grafana 대시보드 구성

### 6.1 추천 공식 대시보드

| 대시보드 ID | 이름 | 용도 |
|-------------|------|------|
| `13639` | [Logs / App](https://grafana.com/grafana/dashboards/13639-logs-app/) | 앱 로그 조회 기본 템플릿 |
| `7752` | [Logging Dashboard](https://grafana.com/grafana/dashboards/7752-logging-dashboard/) | 로그 집계 대시보드 |
| `19268` | [Grafana Alloy](https://grafana.com/grafana/dashboards/19268) | Alloy 에이전트 모니터링 |

### 6.2 권장 대시보드 패널 구성

```
┌─────────────────────────────────────────────────────┐
│  [1] 시간당 로그 발생 건수 (Time series)              │
│      rate({app="python-starter-kit"}[5m])            │
├──────────────────┬──────────────────────────────────┤
│  [2] 레벨별 분포  │  [3] 서비스별 에러 건수           │
│  (Pie Chart)     │  (Bar Chart by service label)    │
├──────────────────┴──────────────────────────────────┤
│  [4] 실시간 ERROR 로그 스트림 (Logs Panel)            │
│      {app="python-starter-kit", level="error"}       │
└─────────────────────────────────────────────────────┘
```

---

## 7. 구현 로드맵

### Phase 1 (현재): 파일 기반 로깅 + Alloy 구성

- [x] `structlog` 의존성 추가
- [x] Docker Compose에 Loki 구성
- [ ] `src/core/logging.py` 구현 (RotatingFileHandler + structlog JSON)
- [ ] `config/alloy/config.alloy` 작성
- [ ] Docker Compose에 Alloy + Grafana 추가
- [ ] `app-logs` 볼륨 공유 설정

### Phase 2: 모니터링 고도화

- [ ] Grafana 대시보드 프로비저닝 자동화 (JSON 파일)
- [ ] ERROR 알림 설정 (Grafana → Slack)
- [ ] LogQL 핵심 지표 대시보드 구성

### Phase 3 (선택): 확장

- [ ] Prometheus 연동 (메트릭 통합)
- [ ] OpenTelemetry Tracing 통합 (Tempo)

---

## 8. 참고 자료

### 공식 문서
- [Grafana Loki 공식 문서](https://grafana.com/docs/loki/latest/)
- [Grafana Alloy 공식 문서](https://grafana.com/docs/alloy/latest/)
- [Alloy - Promtail 마이그레이션 가이드](https://grafana.com/docs/loki/latest/setup/migrate/migrate-to-alloy/)
- [LogQL 공식 문서](https://grafana.com/docs/loki/latest/query/)
- [structlog 공식 문서](https://www.structlog.org/en/stable/logging-best-practices.html)

### 튜토리얼 및 가이드
- [All things logs: best practices for logging and Grafana Loki](https://grafana.com/blog/2022/05/16/all-things-logs-best-practices-for-logging-and-grafana-loki/)
- [How to Collect Application Logs with Loki](https://oneuptime.com/blog/post/2026-01-21-loki-application-logs/view)
- [How to Run Loki in Docker and Docker Compose](https://oneuptime.com/blog/post/2026-01-21-loki-docker-compose/view)
- [A Comprehensive Guide to Python Logging with Structlog](https://betterstack.com/community/guides/logging/structlog/)
- [LogQL: A Primer on Querying Loki from Grafana](https://helgeklein.com/blog/logql-a-primer-on-querying-loki-from-grafana/)
- [Grafana Alloy Part 1: Replacing Promtail](https://www.suse.com/c/grafana-alloy-part-1-replacing-promtail/)
- [Migration From Promtail to Alloy: The What, the Why, and the How](https://developer-friendly.blog/blog/2025/03/17/migration-from-promtail-to-alloy-the-what-the-why-and-the-how/)
- [6 easy ways to improve your log dashboards with Grafana and Loki](https://grafana.com/blog/2023/05/18/6-easy-ways-to-improve-your-log-dashboards-with-grafana-and-grafana-loki/)
