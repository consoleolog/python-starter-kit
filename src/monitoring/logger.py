from pathlib import Path


class StructuredLogger:
    def __init__(self, name: str = "root", config: dict | None = None):
        self.name = name

        default_config = self._default_config()

        if config:
            default_config.update(config)
        self.config = default_config

    @staticmethod
    def _default_config() -> dict:
        return {"log_level": "INFO", "log_dir": "logs"}

    def setup_logging(self):
        log_dir = Path(self.config.get("log_dir", "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
