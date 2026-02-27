import logging

import pytest
import structlog

from src.monitoring.logger import StructuredLogger


@pytest.fixture(autouse=True)
def reset_logging():
    """각 테스트 후 structlog 설정과 root logger 핸들러를 초기화한다."""
    yield
    structlog.reset_defaults()
    root = logging.getLogger()
    for h in root.handlers[:]:
        h.close()
        root.removeHandler(h)


@pytest.mark.unit
def test_default_config():
    """config 인자 없이 생성 시 기본값(log_level=INFO, log_dir=logs)이 적용된다."""
    logger = StructuredLogger(name="app")
    assert logger.config["log_level"] == "INFO"
    assert logger.config["log_dir"] == "logs"


@pytest.mark.unit
def test_custom_config_overrides_defaults():
    """config 인자로 전달한 키는 기본값을 덮어쓰고, 나머지 키는 기본값을 유지한다."""
    logger = StructuredLogger(name="app", config={"log_level": "DEBUG"})
    assert logger.config["log_level"] == "DEBUG"
    assert logger.config["log_dir"] == "logs"  # 기본값 유지


@pytest.mark.unit
def test_log_dir_created_if_missing(tmp_path):
    """log_dir 경로가 존재하지 않아도 setup_logging() 호출 시 자동으로 생성된다."""
    log_dir = tmp_path / "nested" / "logs"
    logger = StructuredLogger(name="app", config={"log_dir": str(log_dir)})
    logger.setup_logging()
    assert log_dir.exists()


@pytest.mark.unit
def test_log_dir_already_exists_no_error(tmp_path):
    """log_dir이 이미 존재할 때 setup_logging()을 호출해도 예외가 발생하지 않는다."""
    logger = StructuredLogger(name="app", config={"log_dir": str(tmp_path)})
    logger.setup_logging()
    logger.setup_logging()  # 두 번 호출해도 예외 없음
