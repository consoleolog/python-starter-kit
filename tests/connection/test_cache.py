import pytest

from src.connection.cache import RedisClient

# ---------------------------------------------------------------------------
# FakeRedisClient
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self, *, raise_on_ping: bool = False, raise_on_config_set: bool = False) -> None:
        self.raise_on_ping = raise_on_ping
        self.raise_on_config_set = raise_on_config_set
        self.closed = False
        self.config_set_calls: list[tuple] = []

    async def ping(self) -> bool:
        if self.raise_on_ping:
            raise ConnectionError("ping 실패")
        return True

    async def config_set(self, key: str, value: str) -> None:
        if self.raise_on_config_set:
            raise Exception("config_set 실패")
        self.config_set_calls.append((key, value))

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE_CONFIG = {"host": "localhost", "port": 6379, "database": 0, "password": "", "max_connections": 10}


@pytest.fixture
def redis_client() -> RedisClient:
    return RedisClient(config=BASE_CONFIG)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestRedisClientInit:
    def test_initial_state(self, redis_client: RedisClient) -> None:
        """초기화 시 client는 None, is_connected는 False, ttl_settings와 channels는 빈 딕셔너리여야 한다."""
        assert redis_client.client is None
        assert redis_client.is_connected is False
        assert redis_client.ttl_settings == {}
        assert redis_client.channels == {}

    def test_config_stored(self, redis_client: RedisClient) -> None:
        """전달한 config 딕셔너리가 그대로 저장되어야 한다."""
        assert redis_client.config == BASE_CONFIG


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, redis_client: RedisClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """연결 성공 시 is_connected가 True가 되고 client가 설정되어야 한다."""
        fake = FakeRedis()
        monkeypatch.setattr("src.connection.cache.redis.from_url", lambda *_, **__: fake)

        await redis_client.connect()

        assert redis_client.is_connected is True
        assert redis_client.client is fake

    @pytest.mark.asyncio
    async def test_url_format_without_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """비밀번호가 없을 때 redis://host:port/db 형식의 URL을 사용해야 한다."""
        captured_urls: list[str] = []
        fake = FakeRedis()

        def fake_from_url(url: str, **_):
            captured_urls.append(url)
            return fake

        monkeypatch.setattr("src.connection.cache.redis.from_url", fake_from_url)

        client = RedisClient(config={**BASE_CONFIG, "password": ""})
        await client.connect()

        assert captured_urls[0] == "redis://localhost:6379/0"

    @pytest.mark.asyncio
    async def test_url_format_with_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """비밀번호가 있을 때 redis://:password@host:port/db 형식의 URL을 사용해야 한다."""
        captured_urls: list[str] = []
        fake = FakeRedis()

        def fake_from_url(url: str, **_):
            captured_urls.append(url)
            return fake

        monkeypatch.setattr("src.connection.cache.redis.from_url", fake_from_url)

        client = RedisClient(config={**BASE_CONFIG, "password": "secret"})
        await client.connect()

        assert captured_urls[0] == "redis://:secret@localhost:6379/0"

    @pytest.mark.asyncio
    async def test_ping_failure_raises_exception(
        self, redis_client: RedisClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ping 실패 시 예외가 전파되고 is_connected는 False로 유지되어야 한다."""
        fake = FakeRedis(raise_on_ping=True)
        monkeypatch.setattr("src.connection.cache.redis.from_url", lambda *_, **__: fake)

        with pytest.raises(ConnectionError):
            await redis_client.connect()

        assert redis_client.is_connected is False

    @pytest.mark.asyncio
    async def test_default_config_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """config에 값이 없을 때 max_connections 기본값 50이 사용되어야 한다."""
        captured_kwargs: list[dict] = []
        fake = FakeRedis()

        def fake_from_url(url: str, **kwargs):
            captured_kwargs.append(kwargs)
            return fake

        monkeypatch.setattr("src.connection.cache.redis.from_url", fake_from_url)

        client = RedisClient(config={})
        await client.connect()

        assert captured_kwargs[0]["max_connections"] == 50


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self, redis_client: RedisClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """disconnect 후 is_connected는 False, client는 None이 되고 연결이 닫혀야 한다."""
        fake = FakeRedis()
        monkeypatch.setattr("src.connection.cache.redis.from_url", lambda *_, **__: fake)
        await redis_client.connect()

        await redis_client.disconnect()

        assert redis_client.is_connected is False
        assert redis_client.client is None
        assert fake.closed is True

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self, redis_client: RedisClient) -> None:
        """미연결 상태에서 disconnect를 호출해도 예외 없이 무시되어야 한다."""
        await redis_client.disconnect()

        assert redis_client.is_connected is False
        assert redis_client.client is None


# ---------------------------------------------------------------------------
# _setup_keyspace_notifications
# ---------------------------------------------------------------------------


class TestSetupKeyspaceNotifications:
    @pytest.mark.asyncio
    async def test_keyspace_notifications_configured(
        self, redis_client: RedisClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """connect 시 notify-keyspace-events가 'Ex'로 설정되어야 한다."""
        fake = FakeRedis()
        monkeypatch.setattr("src.connection.cache.redis.from_url", lambda *_, **__: fake)
        await redis_client.connect()

        assert ("notify-keyspace-events", "Ex") in fake.config_set_calls

    @pytest.mark.asyncio
    async def test_config_set_failure_does_not_propagate(
        self, redis_client: RedisClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """키스페이스 알림 설정 실패 시 예외가 전파되지 않고 connect는 성공해야 한다."""
        fake = FakeRedis(raise_on_config_set=True)
        monkeypatch.setattr("src.connection.cache.redis.from_url", lambda *_, **__: fake)

        await redis_client.connect()

        assert redis_client.is_connected is True
