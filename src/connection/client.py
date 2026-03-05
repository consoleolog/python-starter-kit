import aiohttp
import structlog

logger = structlog.get_logger(__name__)


class HttpClient:
    """aiohttp 기반 HTTP 클라이언트.

    config 예시::

        {
            "connector": {
                "limit": 100,            # 전체 커넥션 풀 크기
                "limit_per_host": 10,    # 호스트당 최대 연결 수
                "keepalive_timeout": 30, # keepalive 유지 시간 (초)
                "enable_cleanup_closed": True,  # 종료된 연결 자동 정리
                "use_dns_cache": True,   # DNS 결과 캐싱 여부
                "ttl_dns_cache": 300,    # DNS 캐시 TTL (초)
            },
            "timeout": {
                "total": 30,            # 요청 전체 타임아웃 (초)
                "connect": 5,           # 커넥션 수립 타임아웃
                "sock_connect": 5,      # 소켓 연결 타임아웃
                "sock_read": 10,        # 소켓 읽기 타임아웃
            },
        }
    """

    def __init__(self, config: dict | None = None):
        """HttpClient를 초기화한다.

        Args:
            config: 커넥터 및 타임아웃 설정 딕셔너리.
                    ``connector``와 ``timeout`` 키를 통해 세부 설정 가능.
        """
        config = config or {}

        self.connector_config = config.get("connector", {})
        self.timeout_config = config.get("timeout", {})

        self._session: aiohttp.ClientSession | None = None
        self._connector: aiohttp.TCPConnector | None = None

    def _build_connector(self) -> aiohttp.TCPConnector:
        """config에서 커넥터 설정을 읽어 TCPConnector를 생성한다.

        Returns:
            커넥션 풀·DNS 캐시 설정이 적용된 TCPConnector 인스턴스.
        """
        return aiohttp.TCPConnector(
            limit=self.connector_config.get("limit", 100),
            limit_per_host=self.connector_config.get("limit_per_host", 10),
            keepalive_timeout=self.connector_config.get("keepalive_timeout", 30),
            enable_cleanup_closed=self.connector_config.get("enable_cleanup_closed", True),
            use_dns_cache=self.connector_config.get("use_dns_cache", True),
            ttl_dns_cache=self.connector_config.get("ttl_dns_cache", 300),
        )

    def _build_timeout(self) -> aiohttp.ClientTimeout:
        """config에서 타임아웃 설정을 읽어 ClientTimeout을 생성한다.

        Returns:
            total·connect·sock_connect·sock_read 타임아웃이 적용된 ClientTimeout 인스턴스.
        """
        return aiohttp.ClientTimeout(
            total=self.timeout_config.get("total", 30),
            connect=self.timeout_config.get("connect", 5),
            sock_connect=self.timeout_config.get("sock_connect", 5),
            sock_read=self.timeout_config.get("sock_read", 10),
        )

    async def connect(self) -> None:
        """새 ClientSession을 생성하고 연결을 수립한다.

        이미 활성화된 세션이 있으면 아무 동작도 하지 않는다.
        세션 생성 시 커넥터·타임아웃 설정이 함께 적용된다.
        """
        if self._session is None or self._session.closed:
            self._connector = self._build_connector()
            self._session = aiohttp.ClientSession(connector=self._connector, timeout=self._build_timeout())
            logger.info(
                "🔗 Connected to Session",
                pool_limit=self.connector_config.get("limit", 100),
                limit_per_host=self.connector_config.get("limit_per_host", 10),
                keepalive_timeout=self.connector_config.get("keepalive_timeout", 30),
                timeout_total=self.timeout_config.get("total", 30),
                timeout_sock_read=self.timeout_config.get("sock_read", 10),
            )

    async def disconnect(self) -> None:
        """활성 세션을 닫고 커넥터를 해제한다.

        이미 닫힌 세션이거나 세션이 없으면 아무 동작도 하지 않는다.
        """
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            self._connector = None
            logger.info("🔌 Disconnected from Session")

    async def _ensure_session(self) -> None:
        """세션이 없거나 닫혀 있으면 connect()를 호출해 세션을 보장한다."""
        if self._session is None or self._session.closed:
            await self.connect()
