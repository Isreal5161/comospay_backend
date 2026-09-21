from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.giftcard_schema import GiftCardSellSubmission
from app.services.giftcard.trading import GiftCardTradingService


def image(content_type: str = "image/jpeg", size: int = 1024) -> SimpleNamespace:
    return SimpleNamespace(content_type=content_type, size=size)


def ecode_submission(**overrides: object) -> GiftCardSellSubmission:
    values: dict[str, object] = {
        "brand_slug": "amazon",
        "card_country": "US",
        "card_type": "ecode",
        "card_currency": "USD",
        "card_amount": 100,
        "additional_info": "AMZN-TEST-1234",
    }
    values.update(overrides)
    return GiftCardSellSubmission(**values)


def test_valid_ecode_submission() -> None:
    submission = ecode_submission()

    assert submission.brand_slug == "amazon"
    assert submission.additional_info == "AMZN-TEST-1234"
    assert submission.payout_currency == "NGN"


@pytest.mark.parametrize(
    "additional_info",
    [None, "short", "x" * 1001],
)
def test_ecode_additional_info_constraints(additional_info: str | None) -> None:
    with pytest.raises(ValidationError):
        ecode_submission(additional_info=additional_info)


def test_valid_physical_submission_with_one_to_five_images() -> None:
    for count in (1, 5):
        submission = GiftCardSellSubmission(
            brand_slug="amazon",
            card_country="US",
            card_type="physical",
            card_currency="USD",
            card_amount=100,
            images=[image()] * count,
        )
        assert submission.images is not None
        assert len(submission.images) == count


@pytest.mark.parametrize(
    "images",
    [None, [], [image()] * 6, [image("image/webp")], [image(size=2 * 1024 * 1024 + 1)]],
)
def test_physical_image_constraints(images: list[SimpleNamespace] | None) -> None:
    with pytest.raises(ValidationError):
        GiftCardSellSubmission(
            brand_slug="amazon",
            card_country="US",
            card_type="physical",
            card_currency="USD",
            card_amount=100,
            images=images,
        )


def test_eu_requires_specific_country() -> None:
    with pytest.raises(ValidationError):
        ecode_submission(card_country="EU")

    submission = ecode_submission(card_country="EU", specific_country="Germany")
    assert submission.specific_country == "Germany"


def test_payout_currency_is_separate_from_card_currency() -> None:
    submission = ecode_submission(card_currency="USD", payout_currency="GBP")

    assert submission.card_currency == "USD"
    assert submission.payout_currency == "GBP"


def test_submission_is_not_in_transaction_metadata_source() -> None:
    source = GiftCardTradingService.sell_giftcard.__code__.co_names

    assert "additional_info" not in source
    assert "images" not in source
