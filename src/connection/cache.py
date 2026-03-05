import pickle  # nosec B403
from typing import Any

import orjson
import redis.asyncio as redis
import structlog
from redis.asyncio.client import Redis

logger = structlog.get_logger(__name__)


class RedisClient:
    """Redis 클라이언트 관리 클래스."""

    def __init__(self, config: dict) -> None:
        """설정 딕셔너리로 초기화합니다.

        Args:
            config: 연결 설정 딕셔너리. 지원하는 키:

                - ``host`` (기본값 ``redis``) - Redis 서버 호스트.
                - ``port`` (int, 기본값 6379) - Redis 서버 포트.
                - ``database`` (int, 기본값 0) - Redis DB 번호.
                - ``password`` - 인증 비밀번호 (없으면 URL에서 생략).
                - ``max_connections`` (int, 기본값 50) - 최대 연결 수.
        """
        self.config = config
        self.client: Redis | None = None
        self.is_connected: bool = False

        # Cache TTL 설정 (초 단위)
        self.ttl_settings = {}

        # Pub/Sub channels 설정
        self.channels = {}

    async def connect(self) -> None:
        """Redis 클라이언트를 생성하고 서버에 연결합니다."""
        try:
            host = self.config.get("host", "redis")
            port = self.config.get("port", 6379)
            database = self.config.get("database", 0)
            password = self.config.get("password", "")

            if password:
                redis_url = f"redis://:{password}@{host}:{port}/{database}"
            else:
                redis_url = f"redis://{host}:{port}/{database}"

            logger.info("🔌 Redis 연결 중", host=host, port=port, database=database)

            self.client = redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=False,  # 디코딩을 직접 처리
                socket_keepalive=True,
                max_connections=self.config.get("max_connections", 50),
                health_check_interval=30,
            )
            await self.client.ping()

            await self._setup_keyspace_notifications()

            self.is_connected = True
            logger.info("✅ Redis 연결 성공", host=host, port=port)

        except Exception as error:
            logger.exception("❌ Redis 연결 실패", error=str(error))
            raise

    async def disconnect(self) -> None:
        """클라이언트 연결을 닫고 연결 상태를 해제합니다."""
        if self.client:
            await self.client.close()
            self.is_connected = False
            self.client = None
            logger.info("🔒 Redis 연결 해제")

    async def _setup_keyspace_notifications(self) -> None:
        """키스페이스 만료 이벤트 알림을 활성화합니다.

        설정 실패 시 경고만 남기고 예외를 전파하지 않습니다.
        """
        try:
            await self.client.config_set("notify-keyspace-events", "Ex")
        except Exception as error:
            logger.exception("⚠️ 키스페이스 알림 설정 실패", error=str(error))

    @staticmethod
    def _deserialize(value: bytes | str) -> Any:
        """바이트 값을 역직렬화합니다.

        JSON → pickle → UTF-8 문자열 순서로 시도합니다.
        """
        try:
            return orjson.loads(value)
        except orjson.JSONDecodeError:
            pass
        try:
            return pickle.loads(value)  # nosec B301
        except Exception:  # nosec B110
            pass
        return value.decode("utf-8") if isinstance(value, bytes) else value

    @staticmethod
    def _serialize(value: Any) -> bytes:
        """값을 바이트로 직렬화합니다.

        dict/list는 JSON으로, str/int/float는 UTF-8로, 그 외 타입은 pickle로 직렬화합니다.
        """
        if isinstance(value, (dict, list)):
            return orjson.dumps(value)
        if isinstance(value, (str, int, float)):
            return str(value).encode("utf-8")
        return pickle.dumps(value)

    # -------------------------------------------------------------------------
    # 단일 키 CRUD
    # -------------------------------------------------------------------------

    async def get(self, key: str) -> Any:
        """키에 해당하는 값을 조회합니다.

        JSON → pickle → 문자열 순서로 역직렬화를 시도합니다.
        값이 없거나 오류 발생 시 None을 반환합니다.
        """
        try:
            value = await self.client.get(key)
            if value is None:
                return None
            return self._deserialize(value)
        except Exception as error:
            logger.exception("Cache get 오류", key=key, error=str(error))
            return None

    async def get_with_options(self, key: str, default: Any = None, decode_json: bool = True) -> Any:
        """옵션을 지정하여 키에 해당하는 값을 조회합니다.

        값이 없으면 default를 반환하고, decode_json=False이면 raw 문자열을 반환합니다.
        """
        try:
            value = await self.client.get(key)
            if value is None:
                return default
            if decode_json:
                return self._deserialize(value)
            return value.decode("utf-8") if isinstance(value, bytes) else value
        except Exception as error:
            logger.exception("Cache get_with_options 오류", key=key, error=str(error))
            return default

    async def set(self, key: str, value: Any, ttl: int = 300, cache_type: str | None = None) -> bool:
        """캐시에 값을 저장합니다.

        Args:
            key: 캐시 키.
            value: 저장할 값. dict/list는 JSON으로, str/int/float는 문자열로,
                   그 외 타입은 pickle로 직렬화합니다.
            ttl: 만료 시간 (초, 기본값 300). 0 이하이면 만료 없이 저장합니다.
            cache_type: ttl_settings에 등록된 타입명. 지정 시 해당 TTL을 우선 적용합니다.

        Returns:
            저장 성공 시 True, 실패 시 False.
        """
        try:
            if cache_type and cache_type in self.ttl_settings:
                ttl = self.ttl_settings.get(cache_type, ttl)

            serialized = self._serialize(value)

            if ttl > 0:
                await self.client.setex(key, ttl, serialized)
            else:
                await self.client.set(key, serialized)

            return True

        except Exception as error:
            logger.exception("Cache set 오류", key=key, error=str(error))
            return False

    async def delete(self, key: str | list[str]) -> int:
        """키(들)를 캐시에서 삭제합니다.

        삭제된 키의 수를 반환합니다.
        """
        try:
            if isinstance(key, str):
                return await self.client.delete(key)
            return await self.client.delete(*key)
        except Exception as error:
            logger.exception("Cache delete 오류", error=str(error))
            return 0

    async def exists(self, key: str) -> bool:
        """키가 캐시에 존재하는지 확인합니다."""
        try:
            return bool(await self.client.exists(key))
        except Exception as error:
            logger.exception("Cache exists 오류", key=key, error=str(error))
            return False

    async def invalidate(self, pattern: str) -> int:
        """패턴에 일치하는 모든 키를 삭제합니다.

        삭제된 키의 수를 반환합니다.
        """
        try:
            keys = [key async for key in self.client.scan_iter(match=pattern)]
            if keys:
                return await self.client.delete(*keys)
            return 0
        except Exception as error:
            logger.exception("Cache invalidate 오류", pattern=pattern, error=str(error))
            return 0

    async def expire(self, key: str, seconds: int) -> bool:
        """키의 만료 시간을 설정합니다."""
        try:
            return await self.client.expire(key, seconds)
        except Exception as error:
            logger.exception("Cache expire 오류", key=key, error=str(error))
            return False

    async def get_ttl(self, key: str) -> int:
        """키의 남은 만료 시간(초)을 반환합니다.

        키가 없거나 만료 시간이 없으면 -1을 반환합니다.
        """
        try:
            return await self.client.ttl(key)
        except Exception as error:
            logger.exception("Cache get_ttl 오류", key=key, error=str(error))
            return -1

    async def clear(self, pattern: str = "*") -> bool:
        """패턴에 일치하는 모든 키를 삭제합니다.

        Args:
            pattern: 삭제할 키 패턴 (기본값 ``*``으로 전체 삭제).

        Returns:
            성공 시 True, 실패 시 False.
        """
        try:
            keys = [key async for key in self.client.scan_iter(match=pattern)]
            if keys:
                deleted = await self.client.delete(*keys)
                logger.info("캐시 삭제 완료", pattern=pattern, deleted=deleted)
            return True
        except Exception as error:
            logger.exception("Cache clear 오류", pattern=pattern, error=str(error))
            return False

    # -------------------------------------------------------------------------
    # 다중 키 CRUD
    # -------------------------------------------------------------------------

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """여러 키의 값을 한 번에 조회합니다.

        None 값은 결과에서 제외합니다.
        """
        try:
            values = await self.client.mget(keys)
            return {key: self._deserialize(value) for key, value in zip(keys, values, strict=False) if value is not None}
        except Exception as error:
            logger.exception("Cache get_many 오류", error=str(error))
            return {}

    async def set_many(self, mapping: dict[str, Any], ttl: int | None = None) -> bool:
        """여러 키-값 쌍을 한 번에 저장합니다.

        pipeline을 사용하여 네트워크 왕복을 최소화합니다.

        Returns:
            저장 성공 시 True, 실패 시 False.
        """
        try:
            async with self.client.pipeline() as pipe:
                for key, value in mapping.items():
                    serialized = self._serialize(value)
                    if ttl and ttl > 0:
                        pipe.setex(key, ttl, serialized)
                    else:
                        pipe.set(key, serialized)
                await pipe.execute()
            return True
        except Exception as error:
            logger.exception("Cache set_many 오류", error=str(error))
            return False

    # -------------------------------------------------------------------------
    # 해시 연산
    # -------------------------------------------------------------------------

    async def hget(self, name: str, key: str) -> Any:
        """해시에서 필드 값을 조회합니다.

        JSON → pickle → 문자열 순서로 역직렬화를 시도합니다.
        """
        try:
            value = await self.client.hget(name, key)
            if value is None:
                return None
            return self._deserialize(value)
        except Exception as error:
            logger.exception("Cache hget 오류", name=name, key=key, error=str(error))
            return None

    async def hset(self, name: str, key: str, value: Any) -> bool:
        """해시에 필드 값을 저장합니다.

        Returns:
            저장 성공 시 True, 실패 시 False.
        """
        try:
            await self.client.hset(name, key, self._serialize(value))
            return True
        except Exception as error:
            logger.exception("Cache hset 오류", name=name, key=key, error=str(error))
            return False

    async def hgetall(self, name: str) -> dict[str, Any]:
        """해시의 모든 필드를 조회합니다.

        각 필드 값에 JSON → pickle → 문자열 순서로 역직렬화를 시도합니다.
        """
        try:
            data = await self.client.hgetall(name)
            return {
                (key.decode("utf-8") if isinstance(key, bytes) else key): self._deserialize(value)
                for key, value in data.items()
            }
        except Exception as error:
            logger.exception("Cache hgetall 오류", name=name, error=str(error))
            return {}

    # -------------------------------------------------------------------------
    # 리스트 연산
    # -------------------------------------------------------------------------

    async def lpush(self, key: str, value: Any) -> int:
        """리스트 왼쪽에 값을 추가합니다.

        추가 후 리스트 길이를 반환합니다.
        """
        try:
            return await self.client.lpush(key, self._serialize(value))
        except Exception as error:
            logger.exception("Cache lpush 오류", key=key, error=str(error))
            return 0

    async def rpop(self, key: str) -> Any | None:
        """리스트 오른쪽에서 값을 꺼냅니다.

        리스트가 비어있거나 오류 발생 시 None을 반환합니다.
        """
        try:
            value = await self.client.rpop(key)
            if value is None:
                return None
            return self._deserialize(value)
        except Exception as error:
            logger.exception("Cache rpop 오류", key=key, error=str(error))
            return None

    async def lrange(self, key: str, start: int, stop: int) -> list[Any]:
        """리스트에서 범위에 해당하는 값들을 조회합니다."""
        try:
            values = await self.client.lrange(key, start, stop)
            return [self._deserialize(v) for v in values]
        except Exception as error:
            logger.exception("Cache lrange 오류", key=key, error=str(error))
            return []

    # -------------------------------------------------------------------------
    # 정렬 집합 연산
    # -------------------------------------------------------------------------

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        """정렬 집합에 멤버와 점수를 추가합니다.

        추가된 멤버 수를 반환합니다.
        """
        try:
            return await self.client.zadd(key, mapping)
        except Exception as error:
            logger.exception("Cache zadd 오류", key=key, error=str(error))
            return 0

    async def zrange(self, key: str, start: int, stop: int, withscores: bool = False) -> list:
        """정렬 집합에서 범위에 해당하는 멤버를 조회합니다."""
        try:
            return await self.client.zrange(key, start, stop, withscores=withscores)
        except Exception as error:
            logger.exception("Cache zrange 오류", key=key, error=str(error))
            return []

    # -------------------------------------------------------------------------
    # Pub/Sub 연산
    # -------------------------------------------------------------------------

    async def publish(self, channel: str, message: Any) -> int:
        """채널에 메시지를 발행합니다.

        메시지를 수신한 구독자 수를 반환합니다.
        """
        try:
            channel_name = self.channels.get(channel, channel)
            serialized = self._serialize(message)
            return await self.client.publish(channel_name, serialized)
        except Exception as error:
            logger.exception("Cache publish 오류", channel=channel, error=str(error))
            return 0

    async def subscribe(self, *channels: str) -> Any:
        """채널을 구독하고 PubSub 객체를 반환합니다."""
        channel_names = [self.channels.get(ch, ch) for ch in channels]
        pubsub = self.client.pubsub()
        await pubsub.subscribe(*channel_names)
        return pubsub

    # -------------------------------------------------------------------------
    # 원자적 연산
    # -------------------------------------------------------------------------

    async def incr(self, key: str, amount: int = 1) -> int:
        """키의 값을 원자적으로 증가시킵니다."""
        try:
            return await self.client.incrby(key, amount)
        except Exception as error:
            logger.exception("Cache incr 오류", key=key, error=str(error))
            return 0

    async def decr(self, key: str, amount: int = 1) -> int:
        """키의 값을 원자적으로 감소시킵니다."""
        try:
            return await self.client.decrby(key, amount)
        except Exception as error:
            logger.exception("Cache decr 오류", key=key, error=str(error))
            return 0

    # -------------------------------------------------------------------------
    # 모니터링
    # -------------------------------------------------------------------------

    async def get_stats(self) -> dict[str, Any]:
        """캐시 통계 정보를 반환합니다."""
        try:
            info = await self.client.info()
            hits = info.get("keyspace_hits", 0)
            misses = info.get("keyspace_misses", 0)
            return {
                "used_memory_mb": info.get("used_memory", 0) / (1024 * 1024),
                "connected_clients": info.get("connected_clients", 0),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "keyspace_hits": hits,
                "keyspace_misses": misses,
                "hit_rate": hits / max(hits + misses, 1) * 100,
                "evicted_keys": info.get("evicted_keys", 0),
                "expired_keys": info.get("expired_keys", 0),
            }
        except Exception as error:
            logger.exception("Cache get_stats 오류", error=str(error))
            return {}

    # -------------------------------------------------------------------------
    # 분산 락
    # -------------------------------------------------------------------------

    async def acquire_lock(self, resource: str, timeout: int = 10, blocking: bool = True) -> Any | None:
        """리소스에 대한 분산 락을 획득합니다.

        획득 성공 시 Lock 객체를 반환하고, 실패 시 None을 반환합니다.
        """
        try:
            lock = self.client.lock(f"lock:{resource}", timeout=timeout, blocking=blocking)
            if await lock.acquire():
                return lock
            return None
        except Exception as error:
            logger.exception("Cache acquire_lock 오류", resource=resource, error=str(error))
            return None

    async def release_lock(self, lock: Any) -> bool:
        """분산 락을 해제합니다.

        Returns:
            해제 성공 시 True, 실패 시 False.
        """
        try:
            await lock.release()
            return True
        except Exception as error:
            logger.exception("Cache release_lock 오류", error=str(error))
            return False
