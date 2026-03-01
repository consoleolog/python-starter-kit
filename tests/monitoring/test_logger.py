import logging

import pytest
import structlog
from structlog.testing import LogCapture, capture_logs

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


# ---------------------------------------------------------------------------
# structlog.testing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_info_event_captured_after_setup(tmp_path):
    """setup_logging() 후 info 이벤트가 올바른 구조로 캡처된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)}).setup_logging()

    with capture_logs() as cap_logs:
        structlog.get_logger().info("login", user_id=42)

    assert cap_logs == [{"event": "login", "user_id": 42, "log_level": "info"}]


@pytest.mark.unit
def test_debug_filtered_at_info_level(tmp_path):
    """log_level=INFO 설정 시 DEBUG 이벤트는 캡처되지 않는다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "log_level": "INFO"}).setup_logging()

    with capture_logs() as cap_logs:
        log = structlog.get_logger()
        log.debug("debug message")
        log.info("info message")

    assert len(cap_logs) == 1
    assert cap_logs[0]["log_level"] == "info"


@pytest.mark.unit
def test_exception_includes_exception_field(tmp_path):
    """except 블록에서 exception() 호출 시 dict_tracebacks가 exception 필드를 구조체로 변환한다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)}).setup_logging()

    cap = LogCapture()
    structlog.configure(processors=[structlog.processors.dict_tracebacks, cap])

    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger().exception("unexpected error")

    entry = cap.entries[0]
    assert entry["log_level"] == "error"
    assert "exc_info" not in entry
    assert entry["exception"][0]["exc_type"] == "ValueError"
    assert entry["exception"][0]["exc_value"] == "boom"
