from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.integrations.giftcards.sogo import SogoGiftCardProvider
from app.integrations.giftcards.sogo_client import SogoAPIError, SogoClient, SogoRequestError
from app.schemas.giftcard_schema import GiftCardSellSubmission


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object], text: str = "") -> None:
        self.status_code = status_code
        self.content = (text or "json").encode()
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def ecode() -> GiftCardSellSubmission:
    return GiftCardSellSubmission(
        brand_slug="amazon",
        card_country="US",
        card_type="ecode",
        card_currency="USD",
        card_amount=100,
        additional_info="AMZN-TEST-1234",
    )


def physical() -> GiftCardSellSubmission:
    images = [
        UploadFile(
            filename="front.jpg",
            file=io.BytesIO(b"front"),
            headers=Headers({"content-type": "image/jpeg"}),
        ),
        UploadFile(
            filename="back.png",
            file=io.BytesIO(b"back"),
            headers=Headers({"content-type": "image/png"}),
        ),
    ]
    return GiftCardSellSubmission(
        brand_slug="amazon",
        card_country="US",
        card_type="physical",
        card_currency="USD",
        card_amount=100,
        additional_info="PHYSICAL-CARD-INFO",
        images=images,
    )


@pytest.mark.asyncio
async def test_client_posts_ecode_as_multipart(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(
        calls=[],
        request=AsyncMock(return_value=FakeResponse(201, {"data": {"reference": "REF"}})),
    )

    def factory(**kwargs: object) -> SimpleNamespace:
        fake.init = kwargs
        return fake

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = SogoClient(base_url=SogoClient.SANDBOX_BASE_URL, api_key="sogo_sk_test_fake")
    await client.sell_gift_card(ecode())

    call = fake.request.await_args
    assert call.args == ("POST", "/gift-cards/sell")
    assert call.kwargs["data"]["slug"] == "amazon"
    assert call.kwargs["data"]["additional_info"] == "AMZN-TEST-1234"
    assert call.kwargs["headers"]["Authorization"] == "Bearer sogo_sk_test_fake"
    assert "Content-Type" not in call.kwargs["headers"]
    assert call.kwargs["files"] == []


@pytest.mark.asyncio
async def test_client_posts_physical_images_as_repeated_multipart_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(request=AsyncMock(return_value=FakeResponse(201, {"data": {}})))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake)
    client = SogoClient(base_url=SogoClient.SANDBOX_BASE_URL, api_key="sogo_sk_test_fake")

    await client.sell_gift_card(physical())

    files = fake.request.await_args.kwargs["files"]
    assert len(files) == 2
    assert [entry[0] for entry in files] == ["images[]", "images[]"]
    assert [entry[1][0] for entry in files] == ["front.jpg", "back.png"]
    assert fake.request.await_args.kwargs["data"]["card_type"] == "physical"


@pytest.mark.asyncio
async def test_client_rejects_non_201_sell_response(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(request=AsyncMock(return_value=FakeResponse(200, {"data": {}})))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake)
    client = SogoClient(base_url=SogoClient.SANDBOX_BASE_URL, api_key="sogo_sk_test_fake")

    with pytest.raises(SogoRequestError, match="unexpected response status"):
        await client.sell_gift_card(ecode())


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 422, 500])
async def test_client_sanitizes_sell_http_errors(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    fake = SimpleNamespace(
        request=AsyncMock(
            return_value=FakeResponse(status_code, {}, text="redemption-code=do-not-expose")
        )
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake)
    client = SogoClient(base_url=SogoClient.SANDBOX_BASE_URL, api_key="sogo_sk_test_fake")

    with pytest.raises(SogoAPIError) as raised:
        await client.sell_gift_card(ecode())
    assert "redemption-code=do-not-expose" not in str(raised.value)


@pytest.mark.asyncio
async def test_provider_maps_sell_response_without_logging_submission() -> None:
    provider = SogoGiftCardProvider(
        base_url=SogoClient.SANDBOX_BASE_URL,
        api_key="sogo_sk_test_fake",
    )
    provider.client.sell_gift_card = AsyncMock(
        return_value={
            "message": "submitted",
            "data": {
                "id": "trade-id",
                "reference": "provider-ref",
                "card_name": "Amazon",
                "card_country": "US",
                "card_type": "ecode",
                "card_amount": 100,
                "card_currency": "USD",
                "status": "pending",
                "payout_amount": {"raw": 125000, "currency": "NGN"},
                "transaction": {"id": "transaction-id"},
            },
        }
    )

    result = await provider.submit_card(card_data={"submission": ecode()})

    assert result["provider_reference"] == "provider-ref"
    assert result["provider_transaction_id"] == "transaction-id"
    assert result["status"] == "pending"
    assert result["card"]["currency"] == "USD"
    assert result["payout_amount"]["currency"] == "NGN"
