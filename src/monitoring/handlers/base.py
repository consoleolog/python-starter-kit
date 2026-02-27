import logging
from abc import ABC, abstractmethod

import structlog


class BaseHandlerBuilder(ABC):
    """structlog ProcessorFormatter 기반 핸들러 빌더 추상 클래스.

    파일/콘솔 핸들러의 공통 로직인 ProcessorFormatter 생성을 담당한다.
    구체적인 핸들러 생성은 서브클래스가 구현한다.
    """

    def __init__(self, shared_processors: list) -> None:
        """
        Args:
            shared_processors: 모든 핸들러에 적용할 공통 프로세서 체인.
        """
        self._shared_processors = shared_processors

    def _make_formatter(self, renderer) -> structlog.stdlib.ProcessorFormatter:
        """renderer를 마지막 단계로 하는 ProcessorFormatter를 생성한다.

        Args:
            renderer: 최종 출력 형식을 결정하는 structlog 프로세서
                      (예: JSONRenderer, ConsoleRenderer).

        Returns:
            설정된 ProcessorFormatter 인스턴스.
        """
        return structlog.stdlib.ProcessorFormatter(
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
            foreign_pre_chain=self._shared_processors,
        )

    @abstractmethod
    def build(self) -> logging.Handler:
        """핸들러 인스턴스를 생성하고 반환한다."""
        pass
