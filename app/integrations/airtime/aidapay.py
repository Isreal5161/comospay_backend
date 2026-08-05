from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from app.config.settings import settings
from app.integrations.airtime.base_provider import VTUProvider
from app.integrations.airtime.exceptions import ProviderTemporaryFailure, ProviderUnavailableError


def _resolve_secret(value: SecretStr | str | None) -> str | None:
	if value is None:
		return None
	if isinstance(value, SecretStr):
		raw = value.get_secret_value()
	else:
		raw = value
	normalized = raw.strip()
	return normalized or None


def _normalize_base_url(value: str | None) -> str | None:
	if value is None:
		return None
	normalized = value.strip().rstrip("/")
	return normalized or None


class AidaPayProvider(VTUProvider):
	"""AidaPay VTU provider contract implementation."""

	def __init__(
		self,
		*,
		api_key: SecretStr | str | None = None,
		base_url: str | None = None,
		account_pin: SecretStr | str | None = None,
		timeout: float | None = None,
		logger: logging.Logger | None = None,
	) -> None:
		self._api_key = _resolve_secret(api_key if api_key is not None else settings.aidapay_api_key)
		resolved_pin = account_pin if account_pin is not None else getattr(settings, "aidapay_account_pin", None)
		self._account_pin = _resolve_secret(resolved_pin)
		self._base_url = _normalize_base_url(base_url if base_url is not None else settings.aidapay_base_url) or ""
		self._timeout = timeout if timeout is not None else self._resolve_timeout()
		self.logger = logger or logging.getLogger(__name__)

	@property
	def name(self) -> str:
		return "aidapay"

	@property
	def priority(self) -> int:
		return 10

	@property
	def is_available(self) -> bool:
		return bool(self._api_key and self._base_url and self._account_pin)

	async def check_balance(self) -> dict[str, Any]:
		return await self._request("GET", "/my_account")

	async def buy_airtime(
		self,
		*,
		phone_number: str,
		network: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		provider_code = await self._resolve_provider_code(service_slug="airtime-topup", value=network)
		return await self._buy(
			recipient=phone_number,
			provider_code=provider_code,
			amount=amount,
			reference=reference,
		)

	async def buy_data(
		self,
		*,
		phone_number: str,
		network: str,
		bundle_code: str,
		reference: str | None = None,
	) -> dict[str, Any]:
		provider_code = await self._resolve_provider_code(service_slug="data-bundle-sme", value=network)
		return await self._buy(
			recipient=phone_number,
			provider_code=provider_code,
			package_code=bundle_code,
			reference=reference,
		)

	async def purchase_electricity(
		self,
		*,
		meter_number: str,
		provider: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		provider_code = await self._resolve_provider_code(service_slug="meter-token", value=provider)
		return await self._buy(
			recipient=meter_number,
			provider_code=provider_code,
			amount=amount,
			reference=reference,
		)

	async def subscribe_tv(
		self,
		*,
		smart_card_number: str,
		provider_code: str,
		package: str,
		package_code: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		resolved_provider_code = provider_code.strip()
		resolved_package_code = package_code.strip()
		if not resolved_provider_code:
			raise ProviderUnavailableError("AidaPay provider_code is required for cable TV purchase.")
		if not resolved_package_code:
			raise ProviderUnavailableError("AidaPay package_code is required for cable TV purchase.")
		return await self._buy(
			recipient=smart_card_number,
			provider_code=resolved_provider_code,
			amount=amount,
			package_code=resolved_package_code,
			reference=reference,
		)

	async def verify_transaction(self, *, provider_reference: str | None = None) -> dict[str, Any]:
		reference = (provider_reference or "").strip()
		if not reference:
			raise ProviderUnavailableError("AidaPay transaction reference is required for verification.")
		return await self._request("GET", f"/transaction/{quote(reference, safe='')}")

	async def health_check(self) -> dict[str, Any]:
		return await self._request("GET", "/my_account")

	async def fetch_supported_networks(self) -> dict[str, Any]:
		return await self._request("GET", "/service/airtime-topup")

	async def fetch_data_plans(self, *, network: str) -> dict[str, Any]:
		provider_code = await self._resolve_provider_code(service_slug="data-bundle-sme", value=network)
		return await self._request("GET", f"/packages/{quote(provider_code, safe='')}")

	async def fetch_electricity_providers(self) -> dict[str, Any]:
		return await self._request("GET", "/service/meter-token")

	async def fetch_cable_tv_providers(self) -> dict[str, Any]:
		return await self._request("GET", "/service/cable-tv-subscription")

	async def fetch_cable_tv_bouquets(self, *, provider_code: str) -> dict[str, Any]:
		normalized = provider_code.strip()
		if not normalized:
			raise ProviderUnavailableError("AidaPay provider_code is required for cable TV bouquets.")
		return await self._request("GET", f"/packages/{quote(normalized, safe='')}")

	async def verify_electricity(self, *, meter_number: str, provider: str) -> dict[str, Any]:
		raise NotImplementedError("AidaPay electricity verification is not implemented yet.")

	async def verify_cable_tv(self, *, smart_card_number: str, provider_code: str, phone: str) -> dict[str, Any]:
		raise NotImplementedError("AidaPay cable TV verification is not implemented yet.")

	async def get_education_price(self, *, service_id: str | int) -> dict[str, Any]:
		raise NotImplementedError("AidaPay education pricing is not implemented yet.")

	async def buy_education_pins(
		self,
		*,
		service_id: str | int,
		phone: str,
		quantity: int,
		product_code: str,
	) -> dict[str, Any]:
		raise NotImplementedError("AidaPay education pin purchase is not implemented yet.")

	def _resolve_timeout(self) -> float:
		connect_timeout = getattr(settings, "connection_timeout", 10)
		read_timeout = getattr(settings, "read_timeout", 10)
		write_timeout = getattr(settings, "write_timeout", 10)
		return float(max(connect_timeout or 0, read_timeout or 0, write_timeout or 0, 1))

	def _headers(self) -> dict[str, str]:
		if not self._api_key:
			raise ProviderUnavailableError("AidaPay API key is not configured.")
		if not self._base_url:
			raise ProviderUnavailableError("AidaPay base URL is not configured.")
		return {
			"Accept": "application/json",
			"Content-Type": "application/json",
			"Authorization": f"Bearer {self._api_key}",
		}

	async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
		url = f"{self._base_url}{path}"
		headers = self._headers()

		try:
			async with httpx.AsyncClient(timeout=self._timeout) as client:
				response = await client.request(method=method, url=url, headers=headers, json=json)
		except httpx.TimeoutException as exc:
			self.logger.warning("aidapay_request_timeout", extra={"path": path, "error": str(exc)})
			raise ProviderTemporaryFailure("AidaPay request timed out.") from exc
		except httpx.RequestError as exc:
			self.logger.warning("aidapay_connection_error", extra={"path": path, "error": str(exc)})
			raise ProviderTemporaryFailure("AidaPay connection failure.") from exc

		payload = self._parse_json(response=response, path=path)
		if response.status_code >= 500:
			message = self._extract_message(payload, fallback="AidaPay is temporarily unavailable.")
			self.logger.warning("aidapay_server_error", extra={"path": path, "status_code": response.status_code, "error_message": message})
			raise ProviderTemporaryFailure(message)
		if response.status_code == 401:
			message = self._extract_message(payload, fallback="AidaPay authentication failed.")
			self.logger.warning("aidapay_auth_error", extra={"path": path, "status_code": response.status_code, "error_message": message})
			raise ProviderUnavailableError(message)
		if response.status_code == 403:
			message = self._extract_message(payload, fallback="AidaPay request forbidden.")
			self.logger.warning("aidapay_forbidden_error", extra={"path": path, "status_code": response.status_code, "error_message": message})
			if "maintenance" in message.lower():
				raise ProviderTemporaryFailure(message)
			raise ProviderUnavailableError(message)
		if response.status_code >= 400:
			message = self._extract_message(payload, fallback="AidaPay request failed.")
			self.logger.warning("aidapay_client_error", extra={"path": path, "status_code": response.status_code, "error_message": message})
			raise ProviderUnavailableError(message)

		if not isinstance(payload, dict):
			self.logger.warning("aidapay_invalid_payload_type", extra={"path": path, "payload_type": str(type(payload))})
			raise ProviderUnavailableError("AidaPay returned an invalid response payload.")

		if "success" not in payload:
			self.logger.warning("aidapay_missing_success_flag", extra={"path": path})
			raise ProviderUnavailableError("AidaPay response missing success flag.")

		if payload.get("success") is False:
			message = self._extract_message(payload, fallback="AidaPay reported request failure.")
			self.logger.warning("aidapay_business_error", extra={"path": path, "error_message": message})
			if "maintenance" in message.lower():
				raise ProviderTemporaryFailure(message)
			raise ProviderUnavailableError(message)

		return payload

	def _parse_json(self, *, response: httpx.Response, path: str) -> Any:
		try:
			return response.json()
		except ValueError as exc:
			self.logger.warning("aidapay_invalid_json", extra={"path": path, "status_code": response.status_code, "error": str(exc)})
			raise ProviderUnavailableError("AidaPay returned malformed JSON.") from exc

	def _extract_message(self, payload: Any, *, fallback: str) -> str:
		if isinstance(payload, dict):
			message = payload.get("message")
			if isinstance(message, str) and message.strip():
				return message.strip()
		return fallback

	def _require_account_pin(self) -> str:
		if not self._account_pin:
			raise ProviderUnavailableError("AidaPay account PIN is not configured.")
		return self._account_pin

	async def _resolve_provider_code(self, *, service_slug: str, value: str) -> str:
		normalized = value.strip().lower()
		if not normalized:
			raise ProviderUnavailableError("AidaPay provider value is required.")

		response = await self._request("GET", f"/service/{service_slug}")
		providers = response.get("data")
		if not isinstance(providers, list):
			raise ProviderUnavailableError("AidaPay provider list is invalid.")

		for item in providers:
			if not isinstance(item, dict):
				continue
			provider_code = str(item.get("provider_code") or "").strip()
			provider_name = str(item.get("provider_name") or "").strip()
			if not provider_code:
				continue
			if normalized in {provider_code.lower(), provider_name.lower()}:
				return provider_code
			if normalized in provider_code.lower() or normalized in provider_name.lower():
				return provider_code

		raise ProviderUnavailableError(f"AidaPay provider not found for '{value}'.")

	def _build_reference(self, reference: str | None) -> str:
		normalized = (reference or "").strip()
		if normalized:
			if len(normalized) < 5:
				raise ProviderUnavailableError("AidaPay reference must be at least 5 characters.")
			return normalized
		return f"AIDA-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"

	async def _buy(
		self,
		*,
		recipient: str,
		provider_code: str,
		reference: str | None,
		amount: float | int | None = None,
		package_code: str | None = None,
	) -> dict[str, Any]:
		normalized_recipient = recipient.strip()
		normalized_provider_code = provider_code.strip()
		if not normalized_recipient:
			raise ProviderUnavailableError("AidaPay recipient is required.")
		if not normalized_provider_code:
			raise ProviderUnavailableError("AidaPay provider_code is required.")

		payload: dict[str, Any] = {
			"recipient": normalized_recipient,
			"provider_code": normalized_provider_code,
			"account_pin": self._require_account_pin(),
			"ref": self._build_reference(reference),
		}
		if amount is not None:
			payload["amount"] = str(amount)
		if package_code is not None:
			normalized_package_code = package_code.strip()
			if normalized_package_code:
				payload["package_code"] = normalized_package_code

		return await self._request("POST", "/buy", json=payload)
