from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.connection.client import HttpClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config() -> dict:
    return {}


@pytest.fixture
def custom_config() -> dict:
    return {
        "connector": {
            "limit": 50,
            "limit_per_host": 5,
            "keepalive_timeout": 15,
            "enable_cleanup_closed": False,
            "use_dns_cache": False,
            "ttl_dns_cache": 60,
        },
        "timeout": {"total": 10, "connect": 2, "sock_connect": 2, "sock_read": 5},
    }


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_init_empty_config_sets_empty_sub_configs(default_config):
    """빈 config로 생성 시 connector_config·timeout_config가 빈 dict로 초기화된다."""
    client = HttpClient(default_config)

    assert client.connector_config == {}
    assert client.timeout_config == {}
    assert client._session is None
    assert client._connector is None


@pytest.mark.unit
def test_init_full_config_parses_sub_configs(custom_config):
    """config에 connector·timeout 키가 있으면 각각 올바르게 파싱된다."""
    client = HttpClient(custom_config)

    assert client.connector_config == custom_config["connector"]
    assert client.timeout_config == custom_config["timeout"]


@pytest.mark.unit
def test_init_partial_config_defaults_missing_keys():
    """connector 키만 있어도 timeout_config는 빈 dict로 초기화된다."""
    client = HttpClient({"connector": {"limit": 20}})

    assert client.connector_config == {"limit": 20}
    assert client.timeout_config == {}


# ---------------------------------------------------------------------------
# _build_connector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_connector_uses_default_values(mocker, default_config):
    """connector config가 없으면 기본값으로 TCPConnector가 생성된다."""
    mock_tcp = mocker.patch("src.connection.client.aiohttp.TCPConnector")

    HttpClient(default_config)._build_connector()

    mock_tcp.assert_called_once_with(
        limit=100,
        limit_per_host=10,
        keepalive_timeout=30,
        enable_cleanup_closed=True,
        use_dns_cache=True,
        ttl_dns_cache=300,
    )


@pytest.mark.unit
def test_build_connector_uses_custom_values(mocker, custom_config):
    """connector config가 설정된 경우 해당 값으로 TCPConnector가 생성된다."""
    mock_tcp = mocker.patch("src.connection.client.aiohttp.TCPConnector")

    HttpClient(custom_config)._build_connector()

    mock_tcp.assert_called_once_with(
        limit=50,
        limit_per_host=5,
        keepalive_timeout=15,
        enable_cleanup_closed=False,
        use_dns_cache=False,
        ttl_dns_cache=60,
    )


@pytest.mark.unit
def test_build_connector_returns_tcp_connector_instance(mocker, default_config):
    """_build_connector()는 TCPConnector 인스턴스를 반환한다."""
    fake_connector = MagicMock(spec=aiohttp.TCPConnector)
    mocker.patch("src.connection.client.aiohttp.TCPConnector", return_value=fake_connector)

    result = HttpClient(default_config)._build_connector()

    assert result is fake_connector


# ---------------------------------------------------------------------------
# _build_timeout
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_timeout_uses_default_values(mocker, default_config):
    """timeout config가 없으면 기본값으로 ClientTimeout이 생성된다."""
    mock_timeout = mocker.patch("src.connection.client.aiohttp.ClientTimeout")

    HttpClient(default_config)._build_timeout()

    mock_timeout.assert_called_once_with(total=30, connect=5, sock_connect=5, sock_read=10)


@pytest.mark.unit
def test_build_timeout_uses_custom_values(mocker, custom_config):
    """timeout config가 설정된 경우 해당 값으로 ClientTimeout이 생성된다."""
    mock_timeout = mocker.patch("src.connection.client.aiohttp.ClientTimeout")

    HttpClient(custom_config)._build_timeout()

    mock_timeout.assert_called_once_with(total=10, connect=2, sock_connect=2, sock_read=5)


@pytest.mark.unit
def test_build_timeout_returns_client_timeout_instance(mocker, default_config):
    """_build_timeout()는 ClientTimeout 인스턴스를 반환한다."""
    fake_timeout = MagicMock(spec=aiohttp.ClientTimeout)
    mocker.patch("src.connection.client.aiohttp.ClientTimeout", return_value=fake_timeout)

    result = HttpClient(default_config)._build_timeout()

    assert result is fake_timeout


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_connect_creates_session_and_connector(mocker, default_config):
    """connect() 호출 시 TCPConnector와 ClientSession이 생성된다."""
    fake_connector = MagicMock()
    fake_session = MagicMock()
    fake_session.closed = False
    mocker.patch("src.connection.client.aiohttp.TCPConnector", return_value=fake_connector)
    mocker.patch("src.connection.client.aiohttp.ClientSession", return_value=fake_session)

    client = HttpClient(default_config)
    await client.connect()

    assert client._connector is fake_connector
    assert client._session is fake_session


@pytest.mark.unit
async def test_connect_passes_connector_and_timeout_to_session(mocker, default_config):
    """connect() 시 ClientSession에 커넥터와 타임아웃이 함께 전달된다."""
    fake_connector = MagicMock()
    fake_timeout = MagicMock()
    mocker.patch("src.connection.client.aiohttp.TCPConnector", return_value=fake_connector)
    mocker.patch("src.connection.client.aiohttp.ClientTimeout", return_value=fake_timeout)
    mock_session_cls = mocker.patch("src.connection.client.aiohttp.ClientSession")

    await HttpClient(default_config).connect()

    mock_session_cls.assert_called_once_with(connector=fake_connector, timeout=fake_timeout)


@pytest.mark.unit
async def test_connect_is_idempotent_when_session_active(mocker, default_config):
    """이미 활성 세션이 있으면 connect()를 재호출해도 새 세션을 생성하지 않는다."""
    mock_session_cls = mocker.patch("src.connection.client.aiohttp.ClientSession")

    client = HttpClient(default_config)
    existing_session = MagicMock()
    existing_session.closed = False
    client._session = existing_session

    await client.connect()

    mock_session_cls.assert_not_called()
    assert client._session is existing_session


@pytest.mark.unit
async def test_connect_reconnects_when_session_closed(mocker, default_config):
    """닫힌 세션이 있으면 connect() 호출 시 새 세션을 생성한다."""
    old_session = MagicMock()
    old_session.closed = True
    new_session = MagicMock()
    new_session.closed = False

    mocker.patch("src.connection.client.aiohttp.TCPConnector", return_value=MagicMock())
    mocker.patch("src.connection.client.aiohttp.ClientSession", return_value=new_session)

    client = HttpClient(default_config)
    client._session = old_session

    await client.connect()

    assert client._session is new_session


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_disconnect_closes_active_session(default_config):
    """활성 세션이 있으면 disconnect() 호출 시 close()가 호출된다."""
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()

    client = HttpClient(default_config)
    client._session = mock_session
    client._connector = MagicMock()

    await client.disconnect()

    mock_session.close.assert_awaited_once()


@pytest.mark.unit
async def test_disconnect_clears_session_and_connector(default_config):
    """disconnect() 후 _session과 _connector가 None으로 초기화된다."""
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()

    client = HttpClient(default_config)
    client._session = mock_session
    client._connector = MagicMock()

    await client.disconnect()

    assert client._session is None
    assert client._connector is None


@pytest.mark.unit
async def test_disconnect_skips_when_no_session(default_config):
    """세션이 없으면 disconnect()는 아무 동작도 하지 않는다."""
    client = HttpClient(default_config)
    client._session = None

    await client.disconnect()  # 예외 없이 통과해야 함

    assert client._session is None


@pytest.mark.unit
async def test_disconnect_skips_when_session_already_closed(default_config):
    """이미 닫힌 세션이면 disconnect()는 close()를 호출하지 않는다."""
    mock_session = MagicMock()
    mock_session.closed = True
    mock_session.close = AsyncMock()

    client = HttpClient(default_config)
    client._session = mock_session

    await client.disconnect()

    mock_session.close.assert_not_awaited()


# ---------------------------------------------------------------------------
# _ensure_session
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_ensure_session_calls_connect_when_no_session(mocker, default_config):
    """세션이 없을 때 _ensure_session()이 connect()를 호출한다."""
    client = HttpClient(default_config)
    mock_connect = mocker.patch.object(client, "connect", new_callable=AsyncMock)

    await client._ensure_session()

    mock_connect.assert_awaited_once()


@pytest.mark.unit
async def test_ensure_session_calls_connect_when_session_closed(mocker, default_config):
    """닫힌 세션이 있을 때 _ensure_session()이 connect()를 호출한다."""
    client = HttpClient(default_config)
    closed_session = MagicMock()
    closed_session.closed = True
    client._session = closed_session

    mock_connect = mocker.patch.object(client, "connect", new_callable=AsyncMock)

    await client._ensure_session()

    mock_connect.assert_awaited_once()


@pytest.mark.unit
async def test_ensure_session_skips_connect_when_session_active(mocker, default_config):
    """활성 세션이 있으면 _ensure_session()은 connect()를 호출하지 않는다."""
    client = HttpClient(default_config)
    active_session = MagicMock()
    active_session.closed = False
    client._session = active_session

    mock_connect = mocker.patch.object(client, "connect", new_callable=AsyncMock)

    await client._ensure_session()

    mock_connect.assert_not_awaited()


# ---------------------------------------------------------------------------
# _request
# ---------------------------------------------------------------------------


@pytest.fixture
def client_with_session(default_config):
    """활성 세션이 주입된 HttpClient를 반환한다."""
    client = HttpClient(default_config)
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.request = AsyncMock(return_value=MagicMock(spec=aiohttp.ClientResponse))
    client._session = mock_session
    return client


@pytest.mark.unit
async def test_request_calls_ensure_session(mocker, default_config):
    """_request() 호출 시 _ensure_session()이 먼저 호출된다."""
    client = HttpClient(default_config)
    mock_ensure = mocker.patch.object(client, "_ensure_session", new_callable=AsyncMock)
    mock_session = MagicMock()
    mock_session.request = AsyncMock(return_value=MagicMock())
    client._session = mock_session

    await client._request("GET", "https://example.com")

    mock_ensure.assert_awaited_once()


@pytest.mark.unit
async def test_request_passes_method_and_url(client_with_session):
    """_request()가 method와 url을 session.request()에 올바르게 전달한다."""
    await client_with_session._request("GET", "https://example.com/users")

    client_with_session._session.request.assert_awaited_once()
    call_args = client_with_session._session.request.call_args
    assert call_args.args == ("GET", "https://example.com/users")


@pytest.mark.unit
async def test_request_uppercases_method(client_with_session):
    """_request()는 소문자 메서드도 대문자로 변환해 전달한다."""
    await client_with_session._request("get", "https://example.com")

    call_args = client_with_session._session.request.call_args
    assert call_args.args[0] == "GET"


@pytest.mark.unit
async def test_request_passes_params(client_with_session):
    """params가 있으면 session.request()에 params 키워드로 전달된다."""
    await client_with_session._request("GET", "https://example.com", params={"page": 1})

    call_kwargs = client_with_session._session.request.call_args.kwargs
    assert call_kwargs["params"] == {"page": 1}


@pytest.mark.unit
async def test_request_omits_params_when_none(client_with_session):
    """params가 None이면 session.request() kwargs에 포함되지 않는다."""
    await client_with_session._request("GET", "https://example.com", params=None)

    call_kwargs = client_with_session._session.request.call_args.kwargs
    assert "params" not in call_kwargs


@pytest.mark.unit
async def test_request_passes_headers(client_with_session):
    """headers가 있으면 session.request()에 headers 키워드로 전달된다."""
    await client_with_session._request("GET", "https://example.com", headers={"X-Token": "abc"})

    call_kwargs = client_with_session._session.request.call_args.kwargs
    assert call_kwargs["headers"] == {"X-Token": "abc"}


@pytest.mark.unit
async def test_request_sends_dict_body_as_json(client_with_session):
    """body가 dict이면 session.request()에 json= 키워드로 전달된다."""
    await client_with_session._request("POST", "https://example.com", body={"name": "Alice"})

    call_kwargs = client_with_session._session.request.call_args.kwargs
    assert call_kwargs["json"] == {"name": "Alice"}
    assert "data" not in call_kwargs


@pytest.mark.unit
async def test_request_sends_json_content_type_body_as_json(client_with_session):
    """Content-Type이 application/json이면 str body도 json=으로 전달된다."""
    await client_with_session._request(
        "POST",
        "https://example.com",
        headers={"Content-Type": "application/json"},
        body='{"name": "Alice"}',
    )

    call_kwargs = client_with_session._session.request.call_args.kwargs
    assert call_kwargs["json"] == '{"name": "Alice"}'
    assert "data" not in call_kwargs


@pytest.mark.unit
async def test_request_sends_str_body_as_data(client_with_session):
    """body가 str이고 Content-Type이 application/json이 아니면 data=로 전달된다."""
    await client_with_session._request("POST", "https://example.com", body="raw text")

    call_kwargs = client_with_session._session.request.call_args.kwargs
    assert call_kwargs["data"] == "raw text"
    assert "json" not in call_kwargs


@pytest.mark.unit
async def test_request_omits_body_when_none(client_with_session):
    """body가 None이면 json과 data 모두 kwargs에 포함되지 않는다."""
    await client_with_session._request("POST", "https://example.com", body=None)

    call_kwargs = client_with_session._session.request.call_args.kwargs
    assert "json" not in call_kwargs
    assert "data" not in call_kwargs


@pytest.mark.unit
async def test_request_returns_client_response(client_with_session):
    """_request()는 session.request()의 반환값을 그대로 반환한다."""
    fake_resp = MagicMock(spec=aiohttp.ClientResponse)
    client_with_session._session.request = AsyncMock(return_value=fake_resp)

    result = await client_with_session._request("GET", "https://example.com")

    assert result is fake_resp


# ---------------------------------------------------------------------------
# get / post / delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_calls_request_with_get_method(mocker, default_config):
    """get()은 _request()를 GET 메서드로 호출한다."""
    client = HttpClient(default_config)
    mock_request = mocker.patch.object(client, "_request", new_callable=AsyncMock)

    await client.get("https://example.com/users")

    mock_request.assert_awaited_once_with("GET", "https://example.com/users", params=None, headers=None)


@pytest.mark.unit
async def test_get_passes_params_and_headers(mocker, default_config):
    """get()은 params와 headers를 _request()에 전달한다."""
    client = HttpClient(default_config)
    mock_request = mocker.patch.object(client, "_request", new_callable=AsyncMock)

    await client.get("https://example.com/users", params={"page": 2}, headers={"X-Token": "abc"})

    mock_request.assert_awaited_once_with(
        "GET", "https://example.com/users", params={"page": 2}, headers={"X-Token": "abc"}
    )


@pytest.mark.unit
async def test_post_calls_request_with_post_method(mocker, default_config):
    """post()는 _request()를 POST 메서드로 호출한다."""
    client = HttpClient(default_config)
    mock_request = mocker.patch.object(client, "_request", new_callable=AsyncMock)

    await client.post("https://example.com/users")

    mock_request.assert_awaited_once_with(
        "POST", "https://example.com/users", params=None, headers=None, body=None
    )


@pytest.mark.unit
async def test_post_passes_body(mocker, default_config):
    """post()는 body를 _request()에 전달한다."""
    client = HttpClient(default_config)
    mock_request = mocker.patch.object(client, "_request", new_callable=AsyncMock)

    await client.post("https://example.com/users", body={"name": "Alice"})

    mock_request.assert_awaited_once_with(
        "POST", "https://example.com/users", params=None, headers=None, body={"name": "Alice"}
    )


@pytest.mark.unit
async def test_delete_calls_request_with_delete_method(mocker, default_config):
    """delete()는 _request()를 DELETE 메서드로 호출한다."""
    client = HttpClient(default_config)
    mock_request = mocker.patch.object(client, "_request", new_callable=AsyncMock)

    await client.delete("https://example.com/users/1")

    mock_request.assert_awaited_once_with("DELETE", "https://example.com/users/1", params=None, headers=None)


@pytest.mark.unit
async def test_delete_passes_params_and_headers(mocker, default_config):
    """delete()는 params와 headers를 _request()에 전달한다."""
    client = HttpClient(default_config)
    mock_request = mocker.patch.object(client, "_request", new_callable=AsyncMock)

    await client.delete("https://example.com/users/1", params={"force": "true"}, headers={"X-Token": "abc"})

    mock_request.assert_awaited_once_with(
        "DELETE", "https://example.com/users/1", params={"force": "true"}, headers={"X-Token": "abc"}
    )
