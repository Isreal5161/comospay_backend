from __future__ import annotations

import logging
from typing import Any

from app.integrations.giftcards.base_giftcard import GiftCardProvider
from app.integrations.giftcards.exceptions import SogoProviderUnavailableError
from app.integrations.giftcards.sogo_client import SogoClient, SogoConfigurationError
from app.schemas.giftcard_schema import GiftCardSellSubmission
from app.utils.exceptions import ValidationException


class SogoGiftCardProvider(GiftCardProvider):
    """Sogo Gift Card provider integration adapter.

    This provider implements read-only gift card operations:
    - get_supported_cards() - Maps catalog to supported cards
    - get_exchange_rate() - Returns rates for a card/amount
    - verify_card() - Not supported in Phase 1
    - submit_card() - Not supported in Phase 1
    - get_transaction_status() - Not supported in Phase 1
    - health_check() - Returns provider status
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        try:
            self.client = SogoClient(base_url=base_url, api_key=api_key, timeout=timeout)
        except SogoConfigurationError as exc:
            raise SogoProviderUnavailableError(f"Sogo configuration error: {exc}") from exc

    async def get_supported_cards(self) -> list[dict[str, Any]]:
        """Return the list of gift card brands supported by Sogo.

        Maps the Sogo catalog response to CosmozPay's card format.
        """
        try:
            response = await self.client.get_catalog()
        except Exception as exc:
            self.logger.warning("sogo_catalog_fetch_failed", extra={"error_type": type(exc).__name__})
            raise SogoProviderUnavailableError("Failed to fetch Sogo catalog.") from exc

        if not isinstance(response, dict):
            raise SogoProviderUnavailableError("Sogo catalog response is malformed.")

        catalog_data = response.get("data")
        if not isinstance(catalog_data, list):
            raise SogoProviderUnavailableError("Sogo catalog data is not a list.")

        # Map Sogo catalog to CosmozPay's supported_cards format
        supported_cards = []
        for card in catalog_data:
            try:
                mapped_card = self._map_catalog_card(card)
                supported_cards.append(mapped_card)
            except Exception as exc:
                self.logger.warning(
                    "sogo_card_mapping_failed",
                    extra={"error_type": type(exc).__name__},
                )
                # Skip malformed cards and continue
                continue

        self.logger.info(
            "sogo_catalog_fetched",
            extra={"card_count": len(supported_cards)},
        )
        return supported_cards

    async def get_exchange_rate(
        self,
        *,
        card_type: str,
        country: str | None = None,
        currency: str | None = None,
        amount: float | int | None = None,
    ) -> dict[str, Any]:
        """Return exchange rate(s) for a specific card.

        The card_type parameter is typically the brand slug (e.g., 'amazon', 'apple').
        Currency defaults to NGN (Nigerian Naira).
        Returns rates keyed by card amount tier if applicable.
        """
        if not card_type or not isinstance(card_type, str):
            raise ValidationException("Card type/slug is required and must be a string.")

        currency = currency or "NGN"

        try:
            response = await self.client.get_rates(slug=card_type)
        except Exception as exc:
            self.logger.warning(
                "sogo_rates_fetch_failed",
                extra={"slug": card_type, "error_type": type(exc).__name__},
            )
            raise SogoProviderUnavailableError("Failed to fetch Sogo rates.") from exc

        if not isinstance(response, dict):
            raise SogoProviderUnavailableError("Sogo rates response is malformed.")

        rates_data = response.get("data")
        if not isinstance(rates_data, list):
            raise SogoProviderUnavailableError("Sogo rates data is not a list.")

        # Find the matching brand in rates response
        matching_brand = None
        for brand in rates_data:
            if isinstance(brand, dict) and brand.get("slug", "").lower() == card_type.lower():
                matching_brand = brand
                break

        if matching_brand is None:
            raise SogoProviderUnavailableError(f"Sogo rates not found for brand: {card_type}")

        # Map the rates response
        mapped_rates = self._map_rates_response(
            brand=matching_brand,
            currency=currency,
            amount=amount,
        )

        self.logger.info(
            "sogo_rates_fetched",
            extra={"slug": card_type, "currency": currency},
        )
        return mapped_rates

    async def verify_card(self, *, card_data: dict[str, Any]) -> dict[str, Any]:
        """Verify a card payload before processing.

        Not supported in Phase 1 of Sogo integration.
        """
        raise SogoProviderUnavailableError("Sogo card verification is not supported in this phase.")

    async def submit_card(self, *, card_data: dict[str, Any]) -> dict[str, Any]:
        """Submit a card for provider processing.

        Extracts the transaction reference from card_data to use as the
        Idempotency-Key header, ensuring Sogo deduplicates identical submissions.
        """
        submission = card_data.get("submission")
        if not isinstance(submission, GiftCardSellSubmission):
            raise ValidationException("A gift card sell submission is required.")

        # Extract the transaction reference to use as idempotency key
        # This ensures that retries with the same reference won't create duplicate submissions
        transaction_reference = card_data.get("reference")

        try:
            response = await self.client.sell_gift_card(
                submission,
                idempotency_key=transaction_reference,
            )
        except Exception as exc:
            self.logger.warning(
                "sogo_sell_submission_failed",
                extra={"error_type": type(exc).__name__},
            )
            raise SogoProviderUnavailableError("Failed to submit gift card to Sogo.") from exc

        if not isinstance(response, dict):
            raise SogoProviderUnavailableError("Sogo sell response is malformed.")
        trade = response.get("data")
        if not isinstance(trade, dict):
            raise SogoProviderUnavailableError("Sogo sell response data is malformed.")

        transaction = trade.get("transaction")
        transaction_id = transaction.get("id") if isinstance(transaction, dict) else None
        return {
            "status": trade.get("status", "pending"),
            "message": response.get("message"),
            "provider_reference": trade.get("reference"),
            "provider_transaction_id": transaction_id or trade.get("id"),
            "card": {
                "name": trade.get("card_name"),
                "country": trade.get("card_country"),
                "type": trade.get("card_type"),
                "sub_type": trade.get("sub_type"),
                "amount": trade.get("card_amount"),
                "currency": trade.get("card_currency"),
            },
            "payout_amount": trade.get("payout_amount"),
            "payout_currency": trade.get("payout_currency"),
            "transaction": transaction,
            "completed_at": trade.get("completed_at"),
            "cancelled_at": trade.get("cancelled_at"),
            "created_at": trade.get("created_at"),
            "provider_metadata": trade,
        }

    async def get_transaction_status(self, *, provider_reference: str | None = None) -> dict[str, Any]:
        """Retrieve the provider transaction status.

        Not supported in Phase 1 of Sogo integration.
        """
        raise SogoProviderUnavailableError("Sogo transaction status lookup is not supported in this phase.")

    async def health_check(self) -> dict[str, Any]:
        """Return the provider health status.

        Performs a lightweight catalog request to verify API connectivity.
        """
        try:
            response = await self.client.get_catalog()
            if isinstance(response, dict) and "data" in response:
                self.logger.info("sogo_health_check_passed")
                return {
                    "status": "healthy",
                    "provider": "sogo",
                    "message": "Sogo API is accessible",
                }
            raise SogoProviderUnavailableError("Sogo health check returned invalid response.")
        except Exception as exc:
            self.logger.warning("sogo_health_check_failed", extra={"error_type": type(exc).__name__})
            return {
                "status": "unhealthy",
                "provider": "sogo",
                "error": "Sogo API is unavailable.",
            }

    def _map_catalog_card(self, card: dict[str, Any]) -> dict[str, Any]:
        """Map a Sogo catalog card to CosmozPay's expected format."""
        return {
            "name": card.get("name", ""),
            "slug": card.get("slug", ""),
            "logo_url": card.get("logo_url"),
            "countries": card.get("countries", []),
            "card_types": card.get("card_types", []),
            "sub_types": card.get("sub_types"),
            "min_amount": card.get("min_amount"),
            "max_amount": card.get("max_amount"),
            "endpoints": card.get("endpoints", {}),
        }

    def _map_rates_response(
        self,
        *,
        brand: dict[str, Any],
        currency: str,
        amount: float | int | None = None,
    ) -> dict[str, Any]:
        """Map Sogo rates response to CosmozPay's expected format.

        The Sogo rates structure is:
        {
            slug: string,
            name: string,
            rates: {
                <currency>: {
                    physical: { NGN: <rate> },
                    ecode: { NGN: <rate> }
                }
                OR array of { min, max, rate } for tiered cards
            }
        }
        """
        rates_obj = brand.get("rates", {})

        # Return the raw rates structure; CosmozPay service will handle interpretation
        return {
            "slug": brand.get("slug", ""),
            "name": brand.get("name", ""),
            "currency": currency,
            "rates": rates_obj,
        }
