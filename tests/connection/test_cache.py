import fnmatch
import pickle
from typing import Any

import orjson
import pytest

from src.connection.cache import RedisClient

# ---------------------------------------------------------------------------
# FakePubSub
# ---------------------------------------------------------------------------


class FakePubSub:
    def __init__(self) -> None:
        self.subscribed: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)


# ---------------------------------------------------------------------------
# FakeLock
# ---------------------------------------------------------------------------


class FakeLock:
    def __init__(self, *, acquired: bool = True, raise_on_release: bool = False) -> None:
        self._acquired = acquired
        self.raise_on_release = raise_on_release
        self.released = False

    async def acquire(self) -> bool:
        return self._acquired

    async def release(self) -> None:
        if self.raise_on_release:
            raise Exception("release 실패")
        self.released = True


# ---------------------------------------------------------------------------
# FakePipeline
# ---------------------------------------------------------------------------


class FakePipeline:
    def __init__(self, fake: "FakeRedis") -> None:
        self._fake = fake
        self._commands: list[tuple] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    def set(self, key: str, value: bytes) -> "FakePipeline":
        self._commands.append(("set", key, value))
        return self

    def setex(self, key: str, ttl: int, value: bytes) -> "FakePipeline":
        self._commands.append(("setex", key, ttl, value))
        return self

    async def execute(self) -> list:
        self._fake._maybe_raise("pipeline")
        for cmd in self._commands:
            if cmd[0] == "set":
                self._fake.store[cmd[1]] = cmd[2]
            elif cmd[0] == "setex":
                self._fake.store[cmd[1]] = cmd[3]
                self._fake.ttls[cmd[1]] = cmd[2]
        return [True] * len(self._commands)


# ---------------------------------------------------------------------------
# FakeRedis
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(
        self,
        *,
        raise_on_ping: bool = False,
        raise_on_config_set: bool = False,
        raise_on: set[str] | None = None,
        lock_acquired: bool = True,
        info_data: dict | None = None,
    ) -> None:
        self.raise_on_ping = raise_on_ping
        self.raise_on_config_set = raise_on_config_set
        self.raise_on: set[str] = raise_on or set()
        self.closed = False
        self.config_set_calls: list[tuple] = []
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.lists: dict[str, list[bytes]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.published: list[tuple[str, bytes]] = []
        self.counters: dict[str, int] = {}
        self._lock = FakeLock(acquired=lock_acquired)
        self._info_data: dict = info_data or {}

    def _maybe_raise(self, method: str) -> None:
        if method in self.raise_on:
            raise Exception(f"{method} 실패")

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

    async def get(self, key: str) -> bytes | None:
        self._maybe_raise("get")
        return self.store.get(key)

    async def set(self, key: str, value: bytes) -> None:
        self._maybe_raise("set")
        self.store[key] = value

    async def setex(self, key: str, ttl: int, value: bytes) -> None:
        self._maybe_raise("setex")
        self.store[key] = value
        self.ttls[key] = ttl

    async def delete(self, *keys: str) -> int:
        self._maybe_raise("delete")
        count = sum(1 for k in keys if k in self.store)
        for k in keys:
            self.store.pop(k, None)
        return count

    async def exists(self, key: str) -> int:
        self._maybe_raise("exists")
        return 1 if key in self.store else 0

    async def expire(self, key: str, seconds: int) -> bool:
        self._maybe_raise("expire")
        self.expiries[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        self._maybe_raise("ttl")
        return self.expiries.get(key, -1)

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        self._maybe_raise("mget")
        return [self.store.get(k) for k in keys]

    async def hget(self, name: str, key: str) -> bytes | None:
        self._maybe_raise("hget")
        return self.hashes.get(name, {}).get(key.encode("utf-8") if isinstance(key, str) else key)

    async def hset(self, name: str, key: str, value: bytes) -> int:
        self._maybe_raise("hset")
        bucket = self.hashes.setdefault(name, {})
        k = key.encode("utf-8") if isinstance(key, str) else key
        is_new = k not in bucket
        bucket[k] = value
        return 1 if is_new else 0

    async def hgetall(self, name: str) -> dict[bytes, bytes]:
        self._maybe_raise("hgetall")
        return self.hashes.get(name, {})

    async def lpush(self, key: str, *values: bytes) -> int:
        self._maybe_raise("lpush")
        lst = self.lists.setdefault(key, [])
        for v in values:
            lst.insert(0, v)
        return len(lst)

    async def rpop(self, key: str) -> bytes | None:
        self._maybe_raise("rpop")
        lst = self.lists.get(key, [])
        return lst.pop() if lst else None

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        self._maybe_raise("lrange")
        lst = self.lists.get(key, [])
        end = len(lst) if stop == -1 else stop + 1
        return lst[start:end]

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._maybe_raise("zadd")
        ss = self.sorted_sets.setdefault(key, {})
        added = sum(1 for m in mapping if m not in ss)
        ss.update(mapping)
        return added

    async def zrange(self, key: str, start: int, stop: int, *, withscores: bool = False) -> list:
        self._maybe_raise("zrange")
        members = sorted(self.sorted_sets.get(key, {}).items(), key=lambda x: x[1])
        end = len(members) if stop == -1 else stop + 1
        sliced = members[start:end]
        if withscores:
            return [(m.encode("utf-8"), s) for m, s in sliced]
        return [m.encode("utf-8") for m, _ in sliced]

    async def publish(self, channel: str, message: bytes) -> int:
        self._maybe_raise("publish")
        self.published.append((channel, message))
        return 1

    def pubsub(self) -> FakePubSub:
        self._pubsub = FakePubSub()
        return self._pubsub

    async def incrby(self, key: str, amount: int) -> int:
        self._maybe_raise("incrby")
        self.counters[key] = self.counters.get(key, 0) + amount
        return self.counters[key]

    async def decrby(self, key: str, amount: int) -> int:
        self._maybe_raise("decrby")
        self.counters[key] = self.counters.get(key, 0) - amount
        return self.counters[key]

    async def info(self) -> dict:
        self._maybe_raise("info")
        return self._info_data

    def lock(self, name: str, **_: Any) -> FakeLock:
        self._maybe_raise("lock")
        return self._lock

    async def scan_iter(self, match: str | None = None):
        self._maybe_raise("scan_iter")
        for key in list(self.store.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE_CONFIG = {"host": "localhost", "port": 6379, "database": 0, "password": "", "max_connections": 10}


@pytest.fixture
def redis_client() -> RedisClient:
    return RedisClient(config=BASE_CONFIG)


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def connected_client(redis_client: RedisClient, fake_redis: FakeRedis) -> RedisClient:
    redis_client.client = fake_redis
    redis_client.is_connected = True
    return redis_client


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


# ---------------------------------------------------------------------------
# _deserialize
# ---------------------------------------------------------------------------


class TestDeserialize:
    def test_deserialize_json_bytes(self, redis_client: RedisClient) -> None:
        """JSON 바이트를 역직렬화하면 dict를 반환해야 한다."""
        data = orjson.dumps({"key": "value"})
        assert redis_client._deserialize(data) == {"key": "value"}

    def test_deserialize_pickle_bytes(self, redis_client: RedisClient) -> None:
        """pickle 바이트를 역직렬화하면 원본 객체를 반환해야 한다."""
        original = {1, 2, 3}
        data = pickle.dumps(original)
        assert redis_client._deserialize(data) == original

    def test_deserialize_plain_string_bytes(self, redis_client: RedisClient) -> None:
        """JSON도 pickle도 아닌 바이트는 UTF-8 문자열로 반환해야 한다."""
        assert redis_client._deserialize(b"hello") == "hello"


# ---------------------------------------------------------------------------
# _serialize
# ---------------------------------------------------------------------------


class TestSerialize:
    def test_serialize_dict_returns_json(self, redis_client: RedisClient) -> None:
        """dict는 JSON 바이트로 직렬화되어야 한다."""
        result = redis_client._serialize({"a": 1})
        assert orjson.loads(result) == {"a": 1}

    def test_serialize_list_returns_json(self, redis_client: RedisClient) -> None:
        """list는 JSON 바이트로 직렬화되어야 한다."""
        result = redis_client._serialize([1, 2, 3])
        assert orjson.loads(result) == [1, 2, 3]

    def test_serialize_str_returns_utf8(self, redis_client: RedisClient) -> None:
        """str은 UTF-8 바이트로 직렬화되어야 한다."""
        assert redis_client._serialize("hello") == b"hello"

    def test_serialize_int_returns_utf8(self, redis_client: RedisClient) -> None:
        """int는 UTF-8 바이트로 직렬화되어야 한다."""
        assert redis_client._serialize(42) == b"42"

    def test_serialize_custom_type_returns_pickle(self, redis_client: RedisClient) -> None:
        """dict/list/str/int/float 외 타입은 pickle 바이트로 직렬화되어야 한다."""
        original = {1, 2, 3}
        result = redis_client._serialize(original)
        assert pickle.loads(result) == original


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.asyncio
    async def test_get_json_value(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """JSON으로 저장된 값을 역직렬화하여 반환해야 한다."""
        fake_redis.store["key"] = orjson.dumps({"a": 1})
        assert await connected_client.get("key") == {"a": 1}

    @pytest.mark.asyncio
    async def test_get_pickle_value(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """pickle로 저장된 값을 역직렬화하여 반환해야 한다."""
        original = {1, 2, 3}
        fake_redis.store["key"] = pickle.dumps(original)
        assert await connected_client.get("key") == original

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none(self, connected_client: RedisClient) -> None:
        """존재하지 않는 키 조회 시 None을 반환해야 한다."""
        assert await connected_client.get("missing") is None

    @pytest.mark.asyncio
    async def test_get_error_returns_none(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 None을 반환해야 한다."""
        fake_redis.raise_on.add("get")
        assert await connected_client.get("key") is None


# ---------------------------------------------------------------------------
# get_with_options
# ---------------------------------------------------------------------------


class TestGetWithOptions:
    @pytest.mark.asyncio
    async def test_decode_json_true_deserializes_value(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """decode_json=True일 때 JSON 값을 역직렬화하여 반환해야 한다."""
        fake_redis.store["key"] = orjson.dumps([1, 2, 3])
        assert await connected_client.get_with_options("key", decode_json=True) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_decode_json_false_returns_raw_string(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """decode_json=False일 때 raw 문자열을 반환해야 한다."""
        fake_redis.store["key"] = b"raw-value"
        assert await connected_client.get_with_options("key", decode_json=False) == "raw-value"

    @pytest.mark.asyncio
    async def test_missing_key_returns_default(self, connected_client: RedisClient) -> None:
        """존재하지 않는 키 조회 시 default 값을 반환해야 한다."""
        assert await connected_client.get_with_options("missing", default="fallback") == "fallback"

    @pytest.mark.asyncio
    async def test_error_returns_default(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 default 값을 반환해야 한다."""
        fake_redis.raise_on.add("get")
        assert await connected_client.get_with_options("key", default=42) == 42


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


class TestSet:
    @pytest.mark.asyncio
    async def test_set_dict_value_serializes_as_json(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """dict 값은 JSON으로 직렬화하고 setex로 저장해야 한다."""
        result = await connected_client.set("key", {"x": 1}, ttl=60)
        assert result is True
        assert orjson.loads(fake_redis.store["key"]) == {"x": 1}
        assert fake_redis.ttls["key"] == 60

    @pytest.mark.asyncio
    async def test_set_str_value_serializes_as_utf8(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """str 값은 UTF-8로 인코딩하여 저장해야 한다."""
        result = await connected_client.set("key", "hello", ttl=60)
        assert result is True
        assert fake_redis.store["key"] == b"hello"

    @pytest.mark.asyncio
    async def test_set_custom_object_serializes_as_pickle(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """dict/list/str/int/float 외 타입은 pickle로 직렬화해야 한다."""
        original = {1, 2, 3}
        result = await connected_client.set("key", original, ttl=60)
        assert result is True
        assert pickle.loads(fake_redis.store["key"]) == original

    @pytest.mark.asyncio
    async def test_set_ttl_zero_uses_set_not_setex(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """ttl=0이면 만료 없이 set 명령을 사용해야 한다."""
        await connected_client.set("key", "value", ttl=0)
        assert "key" in fake_redis.store
        assert "key" not in fake_redis.ttls

    @pytest.mark.asyncio
    async def test_set_cache_type_overrides_ttl(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """cache_type이 ttl_settings에 있으면 해당 TTL을 사용해야 한다."""
        connected_client.ttl_settings["short"] = 10
        await connected_client.set("key", "value", ttl=300, cache_type="short")
        assert fake_redis.ttls["key"] == 10

    @pytest.mark.asyncio
    async def test_set_error_returns_false(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 False를 반환해야 한다."""
        fake_redis.raise_on.add("setex")
        assert await connected_client.set("key", "value", ttl=60) is False


# ---------------------------------------------------------------------------
# set_many
# ---------------------------------------------------------------------------


class TestSetMany:
    @pytest.mark.asyncio
    async def test_set_many_with_ttl(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """TTL을 지정하면 모든 키를 setex로 저장해야 한다."""
        result = await connected_client.set_many({"a": {"x": 1}, "b": "hello"}, ttl=60)
        assert result is True
        assert orjson.loads(fake_redis.store["a"]) == {"x": 1}
        assert fake_redis.store["b"] == b"hello"
        assert fake_redis.ttls["a"] == 60
        assert fake_redis.ttls["b"] == 60

    @pytest.mark.asyncio
    async def test_set_many_without_ttl(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """TTL 없이 저장하면 set 명령을 사용해야 한다."""
        await connected_client.set_many({"a": 1, "b": 2})
        assert b"1" in fake_redis.store["a"]
        assert "a" not in fake_redis.ttls

    @pytest.mark.asyncio
    async def test_set_many_error_returns_false(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """pipeline 오류 발생 시 False를 반환해야 한다."""
        fake_redis.raise_on.add("pipeline")
        assert await connected_client.set_many({"a": 1}) is False


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_single_key(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """단일 키 삭제 시 삭제된 수(1)를 반환하고 키가 제거되어야 한다."""
        fake_redis.store["key"] = b"value"
        result = await connected_client.delete("key")
        assert result == 1
        assert "key" not in fake_redis.store

    @pytest.mark.asyncio
    async def test_delete_multiple_keys(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """여러 키 삭제 시 삭제된 수를 반환해야 한다."""
        fake_redis.store["a"] = b"1"
        fake_redis.store["b"] = b"2"
        result = await connected_client.delete(["a", "b", "missing"])
        assert result == 2
        assert "a" not in fake_redis.store
        assert "b" not in fake_redis.store

    @pytest.mark.asyncio
    async def test_delete_error_returns_zero(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 0을 반환해야 한다."""
        fake_redis.raise_on.add("delete")
        assert await connected_client.delete("key") == 0


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


class TestExists:
    @pytest.mark.asyncio
    async def test_exists_key_present(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """키가 존재하면 True를 반환해야 한다."""
        fake_redis.store["key"] = b"value"
        assert await connected_client.exists("key") is True

    @pytest.mark.asyncio
    async def test_exists_key_missing(self, connected_client: RedisClient) -> None:
        """키가 없으면 False를 반환해야 한다."""
        assert await connected_client.exists("missing") is False

    @pytest.mark.asyncio
    async def test_exists_error_returns_false(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 False를 반환해야 한다."""
        fake_redis.raise_on.add("exists")
        assert await connected_client.exists("key") is False


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------


class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_matching_keys(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """패턴에 일치하는 키를 삭제하고 삭제된 수를 반환해야 한다."""
        fake_redis.store["user:1"] = b"a"
        fake_redis.store["user:2"] = b"b"
        fake_redis.store["order:1"] = b"c"
        result = await connected_client.invalidate("user:*")
        assert result == 2
        assert "user:1" not in fake_redis.store
        assert "order:1" in fake_redis.store

    @pytest.mark.asyncio
    async def test_invalidate_no_matches_returns_zero(self, connected_client: RedisClient) -> None:
        """패턴에 일치하는 키가 없으면 0을 반환해야 한다."""
        assert await connected_client.invalidate("no:match:*") == 0

    @pytest.mark.asyncio
    async def test_invalidate_error_returns_zero(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 0을 반환해야 한다."""
        fake_redis.raise_on.add("scan_iter")
        assert await connected_client.invalidate("*") == 0


# ---------------------------------------------------------------------------
# expire / get_ttl
# ---------------------------------------------------------------------------


class TestExpire:
    @pytest.mark.asyncio
    async def test_expire_sets_expiry(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """expire 호출 시 해당 키의 만료 시간이 설정되고 True를 반환해야 한다."""
        result = await connected_client.expire("key", 120)
        assert result is True
        assert fake_redis.expiries["key"] == 120

    @pytest.mark.asyncio
    async def test_expire_error_returns_false(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 False를 반환해야 한다."""
        fake_redis.raise_on.add("expire")
        assert await connected_client.expire("key", 60) is False


class TestGetTTL:
    @pytest.mark.asyncio
    async def test_get_ttl_returns_remaining_time(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """설정된 만료 시간을 반환해야 한다."""
        fake_redis.expiries["key"] = 300
        assert await connected_client.get_ttl("key") == 300

    @pytest.mark.asyncio
    async def test_get_ttl_error_returns_negative_one(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """Redis 오류 발생 시 -1을 반환해야 한다."""
        fake_redis.raise_on.add("ttl")
        assert await connected_client.get_ttl("key") == -1


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_deletes_matching_keys(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """패턴에 일치하는 모든 키를 삭제하고 True를 반환해야 한다."""
        fake_redis.store["a"] = b"1"
        fake_redis.store["b"] = b"2"
        result = await connected_client.clear("*")
        assert result is True
        assert len(fake_redis.store) == 0

    @pytest.mark.asyncio
    async def test_clear_no_matches_returns_true(self, connected_client: RedisClient) -> None:
        """일치하는 키가 없어도 True를 반환해야 한다."""
        assert await connected_client.clear("no:match:*") is True

    @pytest.mark.asyncio
    async def test_clear_error_returns_false(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 False를 반환해야 한다."""
        fake_redis.raise_on.add("scan_iter")
        assert await connected_client.clear("*") is False


# ---------------------------------------------------------------------------
# get_many
# ---------------------------------------------------------------------------


class TestGetMany:
    @pytest.mark.asyncio
    async def test_get_many_returns_deserialized_values(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """여러 키의 값을 한 번에 역직렬화하여 반환해야 한다."""
        fake_redis.store["a"] = orjson.dumps({"x": 1})
        fake_redis.store["b"] = orjson.dumps([1, 2])
        assert await connected_client.get_many(["a", "b"]) == {"a": {"x": 1}, "b": [1, 2]}

    @pytest.mark.asyncio
    async def test_get_many_excludes_none_values(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """값이 None인 키는 결과에서 제외해야 한다."""
        fake_redis.store["a"] = orjson.dumps(1)
        result = await connected_client.get_many(["a", "missing"])
        assert "missing" not in result
        assert result["a"] == 1

    @pytest.mark.asyncio
    async def test_get_many_error_returns_empty_dict(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """Redis 오류 발생 시 빈 딕셔너리를 반환해야 한다."""
        fake_redis.raise_on.add("mget")
        assert await connected_client.get_many(["a", "b"]) == {}


# ---------------------------------------------------------------------------
# hget / hset / hgetall
# ---------------------------------------------------------------------------


class TestHGet:
    @pytest.mark.asyncio
    async def test_hget_existing_field(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """해시에 필드가 존재하면 역직렬화된 값을 반환해야 한다."""
        fake_redis.hashes["user:1"] = {b"name": orjson.dumps("Alice")}
        assert await connected_client.hget("user:1", "name") == "Alice"

    @pytest.mark.asyncio
    async def test_hget_missing_field_returns_none(self, connected_client: RedisClient) -> None:
        """필드가 없으면 None을 반환해야 한다."""
        assert await connected_client.hget("user:1", "missing") is None

    @pytest.mark.asyncio
    async def test_hget_error_returns_none(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 None을 반환해야 한다."""
        fake_redis.raise_on.add("hget")
        assert await connected_client.hget("user:1", "name") is None


class TestHSet:
    @pytest.mark.asyncio
    async def test_hset_dict_value_serializes_as_json(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """dict 값은 JSON으로 직렬화하여 해시에 저장해야 한다."""
        result = await connected_client.hset("user:1", "data", {"age": 30})
        assert result is True
        assert orjson.loads(fake_redis.hashes["user:1"][b"data"]) == {"age": 30}

    @pytest.mark.asyncio
    async def test_hset_custom_object_serializes_as_pickle(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """dict/list/str/int/float 외 타입은 pickle로 직렬화하여 저장해야 한다."""
        original = {1, 2, 3}
        await connected_client.hset("key", "field", original)
        assert pickle.loads(fake_redis.hashes["key"][b"field"]) == original

    @pytest.mark.asyncio
    async def test_hset_error_returns_false(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 False를 반환해야 한다."""
        fake_redis.raise_on.add("hset")
        assert await connected_client.hset("key", "field", "value") is False


class TestHGetAll:
    @pytest.mark.asyncio
    async def test_hgetall_returns_all_fields(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """해시의 모든 필드를 역직렬화하여 반환해야 한다."""
        fake_redis.hashes["user:1"] = {b"name": orjson.dumps("Alice"), b"age": orjson.dumps(30)}
        assert await connected_client.hgetall("user:1") == {"name": "Alice", "age": 30}

    @pytest.mark.asyncio
    async def test_hgetall_empty_hash_returns_empty_dict(self, connected_client: RedisClient) -> None:
        """해시가 비어있으면 빈 딕셔너리를 반환해야 한다."""
        assert await connected_client.hgetall("empty") == {}

    @pytest.mark.asyncio
    async def test_hgetall_error_returns_empty_dict(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 빈 딕셔너리를 반환해야 한다."""
        fake_redis.raise_on.add("hgetall")
        assert await connected_client.hgetall("user:1") == {}


# ---------------------------------------------------------------------------
# lpush / rpop / lrange
# ---------------------------------------------------------------------------


class TestLPush:
    @pytest.mark.asyncio
    async def test_lpush_returns_list_length(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """lpush 후 리스트 길이를 반환해야 한다."""
        result = await connected_client.lpush("queue", {"task": 1})
        assert result == 1

    @pytest.mark.asyncio
    async def test_lpush_error_returns_zero(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 0을 반환해야 한다."""
        fake_redis.raise_on.add("lpush")
        assert await connected_client.lpush("queue", "item") == 0


class TestRPop:
    @pytest.mark.asyncio
    async def test_rpop_returns_deserialized_value(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """리스트 오른쪽 값을 역직렬화하여 반환해야 한다."""
        fake_redis.lists["queue"] = [orjson.dumps({"task": 1})]
        assert await connected_client.rpop("queue") == {"task": 1}

    @pytest.mark.asyncio
    async def test_rpop_empty_list_returns_none(self, connected_client: RedisClient) -> None:
        """리스트가 비어있으면 None을 반환해야 한다."""
        assert await connected_client.rpop("queue") is None

    @pytest.mark.asyncio
    async def test_rpop_error_returns_none(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 None을 반환해야 한다."""
        fake_redis.raise_on.add("rpop")
        assert await connected_client.rpop("queue") is None


class TestLRange:
    @pytest.mark.asyncio
    async def test_lrange_returns_deserialized_values(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """범위에 해당하는 값들을 역직렬화하여 반환해야 한다."""
        fake_redis.lists["list"] = [orjson.dumps(1), orjson.dumps(2), orjson.dumps(3)]
        assert await connected_client.lrange("list", 0, 1) == [1, 2]

    @pytest.mark.asyncio
    async def test_lrange_error_returns_empty_list(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 빈 리스트를 반환해야 한다."""
        fake_redis.raise_on.add("lrange")
        assert await connected_client.lrange("list", 0, -1) == []


# ---------------------------------------------------------------------------
# zadd / zrange
# ---------------------------------------------------------------------------


class TestZAdd:
    @pytest.mark.asyncio
    async def test_zadd_returns_added_count(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """새 멤버를 추가하고 추가된 수를 반환해야 한다."""
        result = await connected_client.zadd("rank", {"alice": 100.0, "bob": 90.0})
        assert result == 2
        assert fake_redis.sorted_sets["rank"]["alice"] == 100.0

    @pytest.mark.asyncio
    async def test_zadd_error_returns_zero(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 0을 반환해야 한다."""
        fake_redis.raise_on.add("zadd")
        assert await connected_client.zadd("rank", {"alice": 100.0}) == 0


class TestZRange:
    @pytest.mark.asyncio
    async def test_zrange_returns_sorted_members(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """점수 오름차순으로 정렬된 멤버를 반환해야 한다."""
        fake_redis.sorted_sets["rank"] = {"alice": 100.0, "bob": 90.0}
        result = await connected_client.zrange("rank", 0, -1)
        assert result[0] == b"bob"
        assert result[1] == b"alice"

    @pytest.mark.asyncio
    async def test_zrange_error_returns_empty_list(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 빈 리스트를 반환해야 한다."""
        fake_redis.raise_on.add("zrange")
        assert await connected_client.zrange("rank", 0, -1) == []


# ---------------------------------------------------------------------------
# publish / subscribe
# ---------------------------------------------------------------------------


class TestPublish:
    @pytest.mark.asyncio
    async def test_publish_sends_to_channel(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """메시지를 채널에 발행하고 수신자 수를 반환해야 한다."""
        result = await connected_client.publish("events", {"type": "login"})
        assert result == 1
        channel, payload = fake_redis.published[0]
        assert channel == "events"
        assert orjson.loads(payload) == {"type": "login"}

    @pytest.mark.asyncio
    async def test_publish_uses_channel_alias(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """channels 맵에 등록된 별칭을 사용해야 한다."""
        connected_client.channels["events"] = "app:events"
        await connected_client.publish("events", "ping")
        assert fake_redis.published[0][0] == "app:events"

    @pytest.mark.asyncio
    async def test_publish_error_returns_zero(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 0을 반환해야 한다."""
        fake_redis.raise_on.add("publish")
        assert await connected_client.publish("events", "msg") == 0


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_uses_channel_alias(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """channels 맵에 등록된 별칭으로 구독해야 한다."""
        connected_client.channels["events"] = "app:events"
        pubsub = await connected_client.subscribe("events")
        assert "app:events" in pubsub.subscribed

    @pytest.mark.asyncio
    async def test_subscribe_returns_pubsub_object(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """구독 후 PubSub 객체를 반환해야 한다."""
        pubsub = await connected_client.subscribe("channel")
        assert "channel" in pubsub.subscribed


# ---------------------------------------------------------------------------
# incr / decr
# ---------------------------------------------------------------------------


class TestIncr:
    @pytest.mark.asyncio
    async def test_incr_increments_by_one(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """기본 증가량(1)으로 값을 증가시키고 결과를 반환해야 한다."""
        result = await connected_client.incr("counter")
        assert result == 1
        assert fake_redis.counters["counter"] == 1

    @pytest.mark.asyncio
    async def test_incr_increments_by_amount(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """지정한 amount만큼 값을 증가시켜야 한다."""
        await connected_client.incr("counter", 5)
        assert fake_redis.counters["counter"] == 5

    @pytest.mark.asyncio
    async def test_incr_error_returns_zero(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 0을 반환해야 한다."""
        fake_redis.raise_on.add("incrby")
        assert await connected_client.incr("counter") == 0


class TestDecr:
    @pytest.mark.asyncio
    async def test_decr_decrements_by_one(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """기본 감소량(1)으로 값을 감소시키고 결과를 반환해야 한다."""
        result = await connected_client.decr("counter")
        assert result == -1

    @pytest.mark.asyncio
    async def test_decr_decrements_by_amount(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """지정한 amount만큼 값을 감소시켜야 한다."""
        await connected_client.decr("counter", 3)
        assert fake_redis.counters["counter"] == -3

    @pytest.mark.asyncio
    async def test_decr_error_returns_zero(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 0을 반환해야 한다."""
        fake_redis.raise_on.add("decrby")
        assert await connected_client.decr("counter") == 0


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.asyncio
    async def test_get_stats_returns_correct_fields(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis info를 기반으로 통계 딕셔너리를 반환해야 한다."""
        fake_redis._info_data = {
            "used_memory": 1024 * 1024,
            "connected_clients": 5,
            "total_commands_processed": 100,
            "keyspace_hits": 80,
            "keyspace_misses": 20,
            "evicted_keys": 0,
            "expired_keys": 10,
        }
        stats = await connected_client.get_stats()
        assert stats["used_memory_mb"] == 1.0
        assert stats["connected_clients"] == 5
        assert stats["hit_rate"] == 80.0
        assert stats["keyspace_hits"] == 80

    @pytest.mark.asyncio
    async def test_get_stats_error_returns_empty_dict(
        self, connected_client: RedisClient, fake_redis: FakeRedis
    ) -> None:
        """Redis 오류 발생 시 빈 딕셔너리를 반환해야 한다."""
        fake_redis.raise_on.add("info")
        assert await connected_client.get_stats() == {}


# ---------------------------------------------------------------------------
# acquire_lock / release_lock
# ---------------------------------------------------------------------------


class TestAcquireLock:
    @pytest.mark.asyncio
    async def test_acquire_lock_success(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """락 획득 성공 시 Lock 객체를 반환해야 한다."""
        lock = await connected_client.acquire_lock("resource")
        assert lock is fake_redis._lock

    @pytest.mark.asyncio
    async def test_acquire_lock_failure_returns_none(self, redis_client: RedisClient) -> None:
        """락 획득 실패 시 None을 반환해야 한다."""
        fake = FakeRedis(lock_acquired=False)
        redis_client.client = fake
        assert await redis_client.acquire_lock("resource") is None

    @pytest.mark.asyncio
    async def test_acquire_lock_error_returns_none(self, connected_client: RedisClient, fake_redis: FakeRedis) -> None:
        """Redis 오류 발생 시 None을 반환해야 한다."""
        fake_redis.raise_on.add("lock")
        assert await connected_client.acquire_lock("resource") is None


class TestReleaseLock:
    @pytest.mark.asyncio
    async def test_release_lock_success(self, connected_client: RedisClient) -> None:
        """락 해제 성공 시 True를 반환하고 lock.released가 True여야 한다."""
        lock = FakeLock()
        result = await connected_client.release_lock(lock)
        assert result is True
        assert lock.released is True

    @pytest.mark.asyncio
    async def test_release_lock_error_returns_false(self, connected_client: RedisClient) -> None:
        """락 해제 실패 시 False를 반환해야 한다."""
        lock = FakeLock(raise_on_release=True)
        assert await connected_client.release_lock(lock) is False
