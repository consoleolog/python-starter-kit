import logging
import logging.handlers

import structlog

from .base import BaseHandlerBuilder


class FileHandlerBuilder(BaseHandlerBuilder):
    """JSON 포맷의 RotatingFileHandler 빌더.

    dev/prod 공통으로 사용하며 Alloy 에이전트가 이 파일을 수집한다.
    """

    def __init__(
            self,
            shared_processors: list,
            app_name: str,
            log_dir: str,
            level: int,
    ) -> None:
        """
        Args:
            shared_processors: 공통 프로세서 체인.
            app_name: 로그 파일명에 사용할 이름 (예: 'python-starter-kit.log').
            log_dir: 로그 파일 저장 경로.
            level: 핸들러에 적용할 최소 로그 레벨.
        """
        super().__init__(shared_processors)
        self._app_name = app_name
        self._log_dir = log_dir
        self._level = level

    def build(self) -> logging.Handler:
        """JSON 포맷이 설정된 RotatingFileHandler를 반환한다."""
        pass
