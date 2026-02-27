import logging
import os

import structlog

from src.monitoring.handlers import ConsoleHandlerBuilder, FileHandlerBuilder

_configured = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
        app_name: str = "python-starter-kit",
        log_dir: str = "logs",
        log_level: str = "DEBUG",
) -> None:
    """structlog 및 stdlib logging 전역 설정.

    dev/prod 공통으로 JSON 파일 핸들러를 등록하고,
    dev 환경에서는 콘솔 핸들러를 추가로 등록한다.

    Args:
        app_name: 로그 파일명 및 레이블에 사용할 애플리케이션 이름.
        log_dir: 로그 파일을 저장할 디렉토리 경로.
        log_level: 최소 로그 레벨 ('DEBUG', 'INFO', 'WARNING', 'ERROR').
    """
    pass


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """structlog 로거를 반환한다.

    setup_logging()이 호출되지 않은 경우 기본값으로 자동 초기화한다.
    개별 모듈에서 직접 실행 시(uv run module.py)에도 로깅이 동작하도록 보장한다.

    Args:
        name: 로거 이름. None이면 호출 모듈 이름을 사용한다.

    Returns:
        설정이 완료된 structlog BoundLogger 인스턴스.
    """
    pass


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _build_shared_processors(env: str) -> list:
    """모든 핸들러에 공통으로 적용되는 structlog 프로세서 체인을 반환한다.

    Args:
        env: 현재 실행 환경 ('development' | 'production').

    Returns:
        structlog 프로세서 리스트.
    """
    pass


def _add_env(env: str):
    """모든 로그 이벤트에 env 필드를 추가하는 structlog 프로세서를 반환한다.

    Alloy가 이 필드를 Loki 레이블로 추출하여 dev/prod 로그를 구분한다.

    Args:
        env: 삽입할 환경 값 ('development' | 'production').

    Returns:
        event_dict에 'env' 키를 추가하는 프로세서 함수.
    """
    pass
