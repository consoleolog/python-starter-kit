import logging

import structlog

from .base import BaseHandlerBuilder


class ConsoleHandlerBuilder(BaseHandlerBuilder):
    """Pretty 포맷의 콘솔 StreamHandler 빌더.

    dev 환경 전용으로 사용하며 컬러 출력을 제공한다.
    """

    def build(self) -> logging.Handler:
        """ConsoleRenderer가 설정된 StreamHandler를 반환한다."""
        pass
