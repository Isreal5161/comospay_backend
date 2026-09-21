from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from app.integrations.giftcards.exceptions import SogoProviderUnavailableError
from app.integrations.giftcards.sogo import SogoGiftCardProvider
from app.integrations.giftcards.sogo_client import (
    SogoAPIError,
    SogoClient,
    SogoConfigurationError,
    SogoRequestError,
)
from app.utils.exceptions import ValidationException


# ============================================================================
# Fixtures & Mocking Utilities
# ============================================================================


class FakeAsyncClientResponse:
    """Mock httpx.Response for testing."""

    def __init__(self, status_code: int, json_data: dict[str, Any] | None = None, text: str = ""):
        self.status_code = status_code
        self.json_data = json_data if json_data is not None else {}
        # Always use text if provided; otherwise, serialize json_data
        if text:
            self.text = text
        else:
            self.text = json.dumps(self.json_data)
        self.content = self.text.encode("utf-8")

    def json(self) -> dict[str, Any]:
        if self.json_data is None:
            raise ValueError("No JSON data")
        return self.json_data


class FakeAsyncClient:
    """Mock httpx.AsyncClient for testing."""

    def __init__(self, **kwargs: Any):
        self.queue: list[FakeAsyncClientResponse | Exception] = []
        self.calls: list[dict[str, Any]] = []
        self.base_url = kwargs.get("base_url")
        self.timeout = kwargs.get("timeout")

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakeAsyncClientResponse:
        """Record the request and return queued response."""
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if not self.queue:
            raise AssertionError("No queued fake response for request")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        pass


@pytest.fixture
def mock_httpx_client(monkeypatch: pytest.MonkeyPatch):
    """Patch httpx.AsyncClient to use FakeAsyncClient."""
    fake_client = FakeAsyncClient()

    def fake_async_client_factory(**kwargs: Any) -> FakeAsyncClient:
        fake_client.base_url = kwargs.get("base_url")
        fake_client.timeout = kwargs.get("timeout")
        return fake_client

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client_factory)
    return fake_client


@pytest.fixture
def sogo_client_with_key() -> SogoClient:
    """Create a SogoClient with explicit key and URL."""
    return SogoClient(
        base_url="https://sandbox.sogo.africa/v1",
        api_key="sogo_sk_test_abc123",
    )


@pytest.fixture
def sogo_provider_with_key() -> SogoGiftCardProvider:
    """Create a SogoGiftCardProvider with explicit key and URL."""
    return SogoGiftCardProvider(
        base_url="https://sandbox.sogo.africa/v1",
        api_key="sogo_sk_test_abc123",
    )


# ============================================================================
# SogoClient Tests
# ============================================================================


class TestSogoClientConfiguration:
    """Test SogoClient configuration resolution."""

    def test_explicit_base_url_is_used(self) -> None:
        """Test that explicit base_url is preferred."""
        client = SogoClient(
            base_url="https://sandbox.sogo.africa/v1/",
            api_key="sogo_sk_test_fake",
        )
        assert client.base_url == "https://sandbox.sogo.africa/v1"

    def test_explicit_api_key_is_used(self) -> None:
        """Test that explicit api_key is used."""
        client = SogoClient(
            base_url="https://sandbox.sogo.africa/v1/",
            api_key="sogo_sk_test_fake",
        )
        assert client.api_key == "sogo_sk_test_fake"

    def test_settings_secretstr_is_resolved_to_actual_key(self) -> None:
        """Configured SecretStr values must be unmasked only for the auth header."""
        with patch("app.integrations.giftcards.sogo_client.settings") as mock_settings:
            mock_settings.sogo_api_base_url = "https://sandbox.sogo.africa/v1"
            mock_settings.sogo_api_key = SecretStr("sogo_sk_test_settings")
            client = SogoClient()

        assert client.api_key == "sogo_sk_test_settings"
        assert client._build_headers()["Authorization"] == "Bearer sogo_sk_test_settings"
        assert "**********" not in client._build_headers()["Authorization"]

    def test_live_and_sandbox_credentials_match_documented_environments(self) -> None:
        """Documented live and sandbox key prefixes are accepted on matching hosts."""
        sandbox = SogoClient(
            base_url="https://sandbox.sogo.africa/v1",
            api_key="sogo_sk_test_fake",
        )
        live = SogoClient(
            base_url="https://api.sogo.africa/v1",
            api_key="sogo_sk_live_fake",
        )

        assert sandbox.base_url.endswith("/v1")
        assert live.base_url.endswith("/v1")

    @pytest.mark.parametrize(
        ("base_url", "api_key"),
        [
            ("https://sandbox.sogo.africa/v1", "sogo_sk_live_fake"),
            ("https://api.sogo.africa/v1", "sogo_sk_test_fake"),
        ],
    )
    def test_cross_environment_credentials_are_rejected(self, base_url: str, api_key: str) -> None:
        with pytest.raises(SogoConfigurationError, match="requires"):
            SogoClient(base_url=base_url, api_key=api_key)

    def test_missing_api_key_raises_error(self) -> None:
        """Test that missing API key raises SogoConfigurationError."""
        with patch("app.integrations.giftcards.sogo_client.settings") as mock_settings:
            mock_settings.sogo_api_key = None
            with pytest.raises(SogoConfigurationError, match="API key"):
                SogoClient(base_url="https://sandbox.sogo.africa/v1")

    def test_default_timeout_is_10_seconds(self) -> None:
        """Test that default timeout is 10.0 seconds."""
        client = SogoClient(
            base_url="https://sandbox.sogo.africa/v1",
            api_key="sogo_sk_test_fake",
        )
        assert client.timeout == 10.0

    def test_explicit_timeout_is_used(self) -> None:
        """Test that explicit timeout is used."""
        client = SogoClient(
            base_url="https://sandbox.sogo.africa/v1",
            api_key="sogo_sk_test_fake",
            timeout=5.0,
        )
        assert client.timeout == 5.0


class TestSogoClientGetCatalog:
    """Test Sogo catalog endpoint."""

    @pytest.mark.asyncio
    async def test_get_catalog_success(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test successful catalog fetch."""
        catalog_response = {
            "data": [
                {
                    "name": "Amazon",
                    "slug": "amazon",
                    "logo_url": "https://example.com/amazon.png",
                    "countries": ["US", "UK"],
                    "card_types": ["physical", "ecode"],
                    "sub_types": None,
                    "min_amount": 5.0,
                    "max_amount": 500.0,
                    "endpoints": {"rates": "GET /v1/gift-cards/sell/rates?slug=amazon", "sell": "POST /v1/gift-cards/sell"},
                }
            ]
        }
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, catalog_response))

        result = await sogo_client_with_key.get_catalog()

        assert result == catalog_response
        assert len(mock_httpx_client.calls) == 1
        call = mock_httpx_client.calls[0]
        assert call["method"] == "GET"
        assert "/gift-cards/sell/catalog" in call["url"]
        assert "Authorization" in call["headers"]
        assert call["headers"]["Authorization"] == "Bearer sogo_sk_test_abc123"

    @pytest.mark.asyncio
    async def test_get_catalog_http_error(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test catalog fetch with HTTP error."""
        error_response = {"error": "unauthorized"}
        mock_httpx_client.queue.append(
            FakeAsyncClientResponse(401, error_response, text="Unauthorized")
        )

        with pytest.raises(SogoAPIError, match="401"):
            await sogo_client_with_key.get_catalog()

    @pytest.mark.asyncio
    async def test_http_error_does_not_expose_raw_response(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        sensitive_body = "secret-account-token=do-not-expose"
        mock_httpx_client.queue.append(FakeAsyncClientResponse(500, {}, text=sensitive_body))

        with caplog.at_level("WARNING"), pytest.raises(SogoAPIError) as raised:
            await sogo_client_with_key.get_catalog()

        assert sensitive_body not in str(raised.value)
        assert sensitive_body not in caplog.text

    @pytest.mark.asyncio
    async def test_get_catalog_timeout(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test catalog fetch with timeout."""
        mock_httpx_client.queue.append(httpx.TimeoutException("timeout"))

        with pytest.raises(SogoRequestError, match="timeout"):
            await sogo_client_with_key.get_catalog()

    @pytest.mark.asyncio
    async def test_get_catalog_malformed_json(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test catalog fetch with malformed JSON."""
        # Simulate a response with content but invalid JSON
        fake_response = FakeAsyncClientResponse(200, {}, text="not json")
        # Override json() to raise ValueError
        fake_response.json_data = {}

        def raise_error():
            raise ValueError("Invalid JSON")

        fake_response.json = raise_error  # type: ignore
        mock_httpx_client.queue.append(fake_response)

        with pytest.raises(SogoRequestError, match="non-JSON"):
            await sogo_client_with_key.get_catalog()


class TestSogoClientGetRates:
    """Test Sogo rates endpoint."""

    @pytest.mark.asyncio
    async def test_get_rates_all_brands(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test fetching rates for all brands."""
        rates_response = {
            "data": [
                {
                    "slug": "amazon",
                    "name": "Amazon",
                    "rates": {
                        "USD": {
                            "physical": {"NGN": 750.0},
                            "ecode": {"NGN": 740.0},
                        }
                    },
                }
            ]
        }
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, rates_response))

        result = await sogo_client_with_key.get_rates()

        assert result == rates_response
        assert len(mock_httpx_client.calls) == 1
        call = mock_httpx_client.calls[0]
        assert call["method"] == "GET"
        assert "/gift-cards/sell/rates" in call["url"]
        assert call["params"] is None

    @pytest.mark.asyncio
    async def test_get_rates_filtered_by_slug(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test fetching rates filtered by brand slug."""
        rates_response = {
            "data": [
                {
                    "slug": "apple",
                    "name": "Apple",
                    "rates": {"USD": {"ecode": {"NGN": 800.0}}},
                }
            ]
        }
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, rates_response))

        result = await sogo_client_with_key.get_rates(slug="apple")

        assert result == rates_response
        assert len(mock_httpx_client.calls) == 1
        call = mock_httpx_client.calls[0]
        assert call["params"] == {"slug": "apple"}

    @pytest.mark.asyncio
    async def test_get_rates_validation_error(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test rates fetch with validation error (422)."""
        mock_httpx_client.queue.append(
            FakeAsyncClientResponse(422, {"error": "invalid_slug"}, text="Validation failed")
        )

        with pytest.raises(SogoAPIError, match="422"):
            await sogo_client_with_key.get_rates(slug="invalid")


class TestSogoClientConnectionHandling:
    """Test connection and error handling."""

    @pytest.mark.asyncio
    async def test_connection_error(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test handling of connection errors."""
        mock_httpx_client.queue.append(httpx.ConnectError("Connection refused"))

        with pytest.raises(SogoRequestError, match="Connection refused"):
            await sogo_client_with_key.get_catalog()

    @pytest.mark.asyncio
    async def test_bearer_auth_header(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test that Bearer authentication is correctly applied."""
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, {"data": []}))

        await sogo_client_with_key.get_catalog()

        call = mock_httpx_client.calls[0]
        auth_header = call["headers"].get("Authorization")
        assert auth_header == "Bearer sogo_sk_test_abc123"

    @pytest.mark.asyncio
    async def test_required_headers(
        self, sogo_client_with_key: SogoClient, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test that required headers are present."""
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, {"data": []}))

        await sogo_client_with_key.get_catalog()

        call = mock_httpx_client.calls[0]
        headers = call["headers"]
        assert headers.get("Accept") == "application/json"
        assert headers.get("Content-Type") == "application/json"


# ============================================================================
# SogoGiftCardProvider Tests
# ============================================================================


class TestSogoProviderGetSupportedCards:
    """Test get_supported_cards method."""

    @pytest.mark.asyncio
    async def test_get_supported_cards_success(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test successful retrieval of supported cards."""
        catalog_response = {
            "data": [
                {
                    "name": "Amazon",
                    "slug": "amazon",
                    "logo_url": "https://example.com/amazon.png",
                    "countries": ["US"],
                    "card_types": ["physical", "ecode"],
                    "sub_types": None,
                    "min_amount": 5.0,
                    "max_amount": 500.0,
                    "endpoints": {"rates": "...", "sell": "..."},
                },
                {
                    "name": "Apple",
                    "slug": "apple",
                    "logo_url": "https://example.com/apple.png",
                    "countries": ["US", "UK"],
                    "card_types": ["ecode"],
                    "sub_types": None,
                    "min_amount": 10.0,
                    "max_amount": 200.0,
                    "endpoints": {"rates": "...", "sell": "..."},
                },
            ]
        }
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, catalog_response))

        cards = await sogo_provider_with_key.get_supported_cards()

        assert len(cards) == 2
        assert cards[0]["slug"] == "amazon"
        assert cards[0]["name"] == "Amazon"
        assert cards[1]["slug"] == "apple"

    @pytest.mark.asyncio
    async def test_get_supported_cards_empty_catalog(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test with empty catalog."""
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, {"data": []}))

        cards = await sogo_provider_with_key.get_supported_cards()

        assert cards == []

    @pytest.mark.asyncio
    async def test_get_supported_cards_malformed_response(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test with malformed catalog response."""
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, {}))  # Missing 'data' key

        with pytest.raises(SogoProviderUnavailableError, match="not a list"):
            await sogo_provider_with_key.get_supported_cards()

    @pytest.mark.asyncio
    async def test_get_supported_cards_api_error(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test API error when fetching catalog."""
        mock_httpx_client.queue.append(FakeAsyncClientResponse(500, {}, text="Internal Server Error"))

        with pytest.raises(SogoProviderUnavailableError, match="Failed to fetch"):
            await sogo_provider_with_key.get_supported_cards()


class TestSogoProviderGetExchangeRate:
    """Test get_exchange_rate method."""

    @pytest.mark.asyncio
    async def test_get_exchange_rate_success(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test successful rate fetch."""
        rates_response = {
            "data": [
                {
                    "slug": "amazon",
                    "name": "Amazon",
                    "rates": {
                        "USD": {
                            "physical": {"NGN": 750.0},
                            "ecode": {"NGN": 740.0},
                        }
                    },
                }
            ]
        }
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, rates_response))

        result = await sogo_provider_with_key.get_exchange_rate(
            card_type="amazon",
            currency="NGN",
            amount=100.0,
        )

        assert result["slug"] == "amazon"
        assert result["name"] == "Amazon"
        assert "rates" in result
        assert result["currency"] == "NGN"

    @pytest.mark.asyncio
    async def test_get_exchange_rate_missing_card_type(
        self, sogo_provider_with_key: SogoGiftCardProvider
    ) -> None:
        """Test validation when card_type is missing."""
        with pytest.raises(ValidationException, match="Card type"):
            await sogo_provider_with_key.get_exchange_rate(card_type="")

    @pytest.mark.asyncio
    async def test_get_exchange_rate_slug_not_found(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test when requested slug is not in rates response."""
        rates_response = {
            "data": [
                {
                    "slug": "apple",
                    "name": "Apple",
                    "rates": {},
                }
            ]
        }
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, rates_response))

        with pytest.raises(SogoProviderUnavailableError, match="not found"):
            await sogo_provider_with_key.get_exchange_rate(card_type="amazon")

    @pytest.mark.asyncio
    async def test_get_exchange_rate_default_currency(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test that currency defaults to NGN."""
        rates_response = {
            "data": [
                {
                    "slug": "amazon",
                    "name": "Amazon",
                    "rates": {"USD": {"ecode": {"NGN": 750.0}}},
                }
            ]
        }
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, rates_response))

        result = await sogo_provider_with_key.get_exchange_rate(card_type="amazon")

        assert result["currency"] == "NGN"


class TestSogoProviderUnsupportedOperations:
    """Test that unsupported Phase 1 operations raise appropriate errors."""

    @pytest.mark.asyncio
    async def test_verify_card_not_supported(self, sogo_provider_with_key: SogoGiftCardProvider) -> None:
        """Test that verify_card raises error."""
        with pytest.raises(SogoProviderUnavailableError, match="not supported"):
            await sogo_provider_with_key.verify_card(card_data={})

    @pytest.mark.asyncio
    async def test_submit_card_requires_sell_submission(self, sogo_provider_with_key: SogoGiftCardProvider) -> None:
        """Test that submit_card requires the Phase 2A submission object."""
        with pytest.raises(ValidationException, match="sell submission"):
            await sogo_provider_with_key.submit_card(card_data={})

    @pytest.mark.asyncio
    async def test_get_transaction_status_not_supported(
        self, sogo_provider_with_key: SogoGiftCardProvider
    ) -> None:
        """Test that get_transaction_status raises error."""
        with pytest.raises(SogoProviderUnavailableError, match="not supported"):
            await sogo_provider_with_key.get_transaction_status(provider_reference="REF123")


class TestSogoProviderHealthCheck:
    """Test health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_success(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test successful health check."""
        mock_httpx_client.queue.append(FakeAsyncClientResponse(200, {"data": []}))

        result = await sogo_provider_with_key.health_check()

        assert result["status"] == "healthy"
        assert result["provider"] == "sogo"

    @pytest.mark.asyncio
    async def test_health_check_api_failure(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        """Test health check when API is unavailable."""
        mock_httpx_client.queue.append(FakeAsyncClientResponse(503, {}, text="Service Unavailable"))

        result = await sogo_provider_with_key.health_check()

        assert result["status"] == "unhealthy"
        assert result["provider"] == "sogo"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_health_check_does_not_expose_raw_response(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient
    ) -> None:
        sensitive_body = "secret-account-token=do-not-expose"
        mock_httpx_client.queue.append(FakeAsyncClientResponse(503, {}, text=sensitive_body))

        result = await sogo_provider_with_key.health_check()

        assert sensitive_body not in str(result)
        assert result["error"] == "Sogo API is unavailable."

    @pytest.mark.asyncio
    async def test_provider_logs_do_not_expose_secret(
        self, sogo_provider_with_key: SogoGiftCardProvider, mock_httpx_client: FakeAsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_httpx_client.queue.append(FakeAsyncClientResponse(500, {}, text="secret-account-token=do-not-expose"))

        with caplog.at_level("WARNING"):
            with pytest.raises(SogoProviderUnavailableError):
                await sogo_provider_with_key.get_supported_cards()

        assert "sogo_sk_test_abc123" not in caplog.text
        assert "secret-account-token=do-not-expose" not in caplog.text


class TestSogoProviderResponseMapping:
    """Test response mapping utilities."""

    def test_map_catalog_card(self, sogo_provider_with_key: SogoGiftCardProvider) -> None:
        """Test _map_catalog_card utility."""
        sogo_card = {
            "name": "Amazon",
            "slug": "amazon",
            "logo_url": "https://example.com/logo.png",
            "countries": ["US", "UK"],
            "card_types": ["physical"],
            "sub_types": None,
            "min_amount": 5.0,
            "max_amount": 500.0,
            "endpoints": {"rates": "...", "sell": "..."},
        }

        mapped = sogo_provider_with_key._map_catalog_card(sogo_card)

        assert mapped["name"] == "Amazon"
        assert mapped["slug"] == "amazon"
        assert mapped["logo_url"] == "https://example.com/logo.png"
        assert mapped["countries"] == ["US", "UK"]

    def test_map_rates_response(self, sogo_provider_with_key: SogoGiftCardProvider) -> None:
        """Test _map_rates_response utility."""
        sogo_rates = {
            "slug": "amazon",
            "name": "Amazon",
            "rates": {
                "USD": {
                    "physical": {"NGN": 750.0},
                    "ecode": {"NGN": 740.0},
                }
            },
        }

        mapped = sogo_provider_with_key._map_rates_response(
            brand=sogo_rates,
            currency="NGN",
        )

        assert mapped["slug"] == "amazon"
        assert mapped["name"] == "Amazon"
        assert mapped["currency"] == "NGN"
        assert "rates" in mapped
