import logging
from pathlib import Path

import structlog


class StructuredLogger:
    def __init__(self, name: str = "root", config: dict | None = None):
        self.name = name

        default_config = self._default_config()

        if config:
            default_config.update(config)
        self.config = default_config
        self.setup_logging()

    @staticmethod
    def _default_config() -> dict:
        return {"log_level": "INFO", "log_dir": "logs"}

    def setup_logging(self):
        log_dir = Path(self.config.get("log_dir", "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)

        level = getattr(logging, self.config.get("log_level", "INFO").upper(), logging.INFO)

        shared_processors = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.dict_tracebacks,
        ]

        structlog.configure(
            processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
