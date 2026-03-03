# 모니터링 가이드

> **PLG 스택(Promtail → Loki → Grafana)으로 애플리케이션 로그를 수집·저장·시각화한다.**

---

## 목차

1. [아키텍처](#1-아키텍처)
2. [환경 구성](#2-환경-구성)
3. [실행 방법](#3-실행-방법)
4. [로그 생성](#4-로그-생성)
5. [Grafana에서 로그 보기](#5-grafana에서-로그-보기)
6. [LogQL 쿼리](#6-logql-쿼리)
7. [환경 변수](#7-환경-변수)

---

## 1. 아키텍처

```
애플리케이션
  └─▶ logs/*.log (JSON)
        └─▶ Alloy (파일 테일링 + JSON 파싱)
              └─▶ Loki (로그 저장)
                    └─▶ Grafana (시각화)
```

| 서비스 | 역할 | 내부 포트 |
|--------|------|----------|
| **Loki** | 로그 저장소 | `3100` |
| **Alloy** | 로그 수집기 (logs/ 테일링 → Loki 전송) | `12345` |
| **Grafana** | 로그 시각화 대시보드 | `3000` |

---

## 2. 환경 구성

### 파일 구조

```
├── docker-compose.monitoring.yaml   # 모니터링 스택 정의
├── .env                             # 환경 변수 (gitignore)
├── .env.example                     # 환경 변수 예시 (커밋됨)
├── config/
│   ├── loki/config.yml              # Loki 설정
│   ├── alloy/config.alloy           # Alloy 파이프라인 설정
│   └── grafana/provisioning/
│       └── datasources/loki.yml    # Grafana Loki 데이터소스 자동 등록
└── logs/                            # 앱 로그 출력 디렉토리 (Alloy가 테일링)
```

### 최초 설정

```bash
# .env.example을 복사하여 .env 생성
cp .env.example .env

# .env에서 Grafana 어드민 비밀번호 변경 (필수)
# GF_ADMIN_PASSWORD=changeme  →  강한 비밀번호로 교체
```

---

## 3. 실행 방법

```bash
# 스택 시작
docker compose -f docker-compose.monitoring.yaml up -d

# 상태 확인
docker compose -f docker-compose.monitoring.yaml ps

# 로그 확인
docker compose -f docker-compose.monitoring.yaml logs -f

# 스택 종료
docker compose -f docker-compose.monitoring.yaml down

# 스택 종료 + 볼륨 삭제 (데이터 초기화)
docker compose -f docker-compose.monitoring.yaml down -v
```

### 정상 상태

```
NAME      STATUS              PORTS
alloy     Up (healthy)        0.0.0.0:12345->12345/tcp
grafana   Up (healthy)        0.0.0.0:3000->3000/tcp
loki      Up (healthy)        0.0.0.0:3100->3100/tcp
```

---

## 4. 로그 생성

애플리케이션은 `src/monitoring/logger.py`의 `StructuredLogger`를 통해 JSON 형식으로 로그를 기록한다.

```python
from src.monitoring.logger import StructuredLogger
import structlog

StructuredLogger(name="my-service")
logger = structlog.get_logger(__name__)

logger.info("서버 시작", port=8080)
logger.error("DB 연결 실패", host="localhost")
```

로그 파일은 `logs/{name}.log`, 에러 로그는 `logs/{name}.error.log`에 저장되며 Alloy가 자동으로 수집한다.

---

## 5. Grafana에서 로그 보기

### 접속

| 항목 | 값 |
|------|-----|
| URL | `http://localhost:3000` |
| ID | `.env`의 `GF_ADMIN_USER` |
| PW | `.env`의 `GF_ADMIN_PASSWORD` |

### 로그 조회 방법

1. 좌측 메뉴 **Explore** 클릭
2. 상단 데이터소스에서 **Loki** 선택
3. **Label filters** 또는 **Code** 탭에서 쿼리 입력
4. 우측 상단 **Run query** 클릭

---

## 6. LogQL 쿼리

### 기본

```logql
# 전체 로그 조회
{logger=~".+"}

# 특정 로거
{logger="__main__"}

# ERROR 레벨만
{level="error"}

# 특정 로거 + ERROR
{logger="my-service", level="error"}
```

### 텍스트 검색

```logql
# 로그 내용에 "timeout" 포함
{logger=~".+"} |= "timeout"

# 정규식
{logger=~".+"} |~ "DB.*실패"
```

### 집계

```logql
# 1분 단위 ERROR 발생 수
count_over_time({level="error"}[1m])

# 로거별 로그 수
sum by (logger) (count_over_time({logger=~".+"}[5m]))
```

---

## 7. 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LOKI_PORT` | `3100` | Loki 호스트 포트 |
| `ALLOY_PORT` | `12345` | Alloy UI 호스트 포트 |
| `GRAFANA_PORT` | `3000` | Grafana 호스트 포트 |
| `GF_ADMIN_USER` | `admin` | Grafana 어드민 계정 |
| `GF_ADMIN_PASSWORD` | `changeme` | Grafana 어드민 비밀번호 **(반드시 변경)** |
