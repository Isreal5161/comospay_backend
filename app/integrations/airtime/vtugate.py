from __future__ import annotations

import logging
from typing import Any

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


class VTUGateProvider(VTUProvider):
	"""VTUGate VTU provider contract implementation."""

	def __init__(
		self,
		*,
		api_key: SecretStr | str | None = None,
		base_url: str | None = None,
		timeout: float | None = None,
		logger: logging.Logger | None = None,
	) -> None:
		self._api_key = _resolve_secret(api_key if api_key is not None else settings.vtugate_api_key)
		self._base_url = (base_url if base_url is not None else settings.vtugate_base_url) or ""
		self._timeout = timeout if timeout is not None else self._resolve_timeout()
		self.logger = logger or logging.getLogger(__name__)

	@property
	def name(self) -> str:
		return "vtugate"

	@property
	def priority(self) -> int:
		return 20

	@property
	def is_available(self) -> bool:
		return bool(self._api_key and self._base_url)

	def _resolve_timeout(self) -> float:
		connect_timeout = getattr(settings, "connection_timeout", 10)
		read_timeout = getattr(settings, "read_timeout", 10)
		write_timeout = getattr(settings, "write_timeout", 10)
		return float(max(connect_timeout or 0, read_timeout or 0, write_timeout or 0, 1))

	def _headers(self) -> dict[str, str]:
		if not self._api_key:
			raise ProviderUnavailableError("VTUGate API key is not configured.")
		if not self._base_url:
			raise ProviderUnavailableError("VTUGate base URL is not configured.")
		return {
			"Content-Type": "application/x-www-form-urlencoded",
			"Authorization": f"Bearer {self._api_key}",
		}

	def _is_temporary_message(self, message: str) -> bool:
		normalized = message.strip().lower()
		if not normalized:
			return False
		keywords = {
			"maintenance",
			"temporar",
			"timeout",
			"unavailable",
			"try again",
			"upstream",
			"service unavailable",
		}
		return any(keyword in normalized for keyword in keywords)

	def _extract_message(self, payload: Any, fallback: str) -> str:
		if isinstance(payload, dict):
			message = payload.get("message")
			if isinstance(message, str) and message.strip():
				return message.strip()
		return fallback

	def _ensure_dict(self, payload: Any) -> dict[str, Any]:
		if not isinstance(payload, dict):
			raise ProviderUnavailableError("VTUGate returned an invalid response payload.")
		return payload

	async def _request(self, *, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
		url = f"{self._base_url.rstrip('/')}{path}"
		headers = self._headers()
		encoded_data = {key: str(value) for key, value in (data or {}).items() if value is not None}

		try:
			async with httpx.AsyncClient(timeout=self._timeout) as client:
				response = await client.post(url=url, headers=headers, data=encoded_data)
		except httpx.TimeoutException as exc:
			self.logger.warning("vtugate_request_timeout", extra={"path": path, "error": str(exc)})
			raise ProviderTemporaryFailure("VTUGate request timed out.") from exc
		except httpx.RequestError as exc:
			self.logger.warning("vtugate_connection_error", extra={"path": path, "error": str(exc)})
			raise ProviderTemporaryFailure("VTUGate connection failure.") from exc

		try:
			payload = response.json()
		except ValueError as exc:
			self.logger.warning("vtugate_invalid_json", extra={"path": path, "status_code": response.status_code, "error": str(exc)})
			raise ProviderUnavailableError("VTUGate returned malformed JSON.") from exc

		payload_dict = self._ensure_dict(payload)
		message = self._extract_message(payload_dict, fallback="VTUGate request failed.")

		if response.status_code >= 500:
			raise ProviderTemporaryFailure(message)
		if response.status_code in {401, 403}:
			raise ProviderUnavailableError(message)
		if response.status_code >= 400:
			if self._is_temporary_message(message):
				raise ProviderTemporaryFailure(message)
			raise ProviderUnavailableError(message)

		status_value = payload_dict.get("status")
		if isinstance(status_value, bool) and status_value is False:
			if self._is_temporary_message(message):
				raise ProviderTemporaryFailure(message)
			raise ProviderUnavailableError(message)

		return payload_dict

	async def _resolve_service_id(self, *, service_type: str, value: str | int) -> str:
		value_text = str(value).strip()
		if not value_text:
			raise ProviderUnavailableError("VTUGate service lookup value is required.")
		if value_text.isdigit():
			return value_text

		services_response = await self.fetch_services(service_type=service_type)
		services = services_response.get("data")
		if not isinstance(services, list):
			raise ProviderUnavailableError("VTUGate services payload is invalid.")

		normalized = value_text.lower()
		for item in services:
			if not isinstance(item, dict):
				continue
			service_id = str(item.get("service_id") or "").strip()
			if not service_id:
				continue
			candidates = [
				str(item.get("network_name") or "").strip().lower(),
				str(item.get("provider") or "").strip().lower(),
				str(item.get("name") or "").strip().lower(),
				str(item.get("service_name") or "").strip().lower(),
				service_id.lower(),
			]
			if normalized in candidates:
				return service_id
			if any(normalized and normalized in candidate for candidate in candidates if candidate):
				return service_id

		raise ProviderUnavailableError(f"VTUGate service was not found for '{value_text}'.")

	def _extract_data_plan(self, *, plans_payload: dict[str, Any], bundle_code: str) -> tuple[str, str]:
		plans = plans_payload.get("data")
		if not isinstance(plans, list):
			raise ProviderUnavailableError("VTUGate data plans payload is invalid.")

		normalized_bundle = str(bundle_code).strip().lower()
		if not normalized_bundle:
			raise ProviderUnavailableError("VTUGate bundle code is required.")

		for plan in plans:
			if not isinstance(plan, dict):
				continue
			plan_code = str(plan.get("plan_code") or plan.get("code") or plan.get("id") or "").strip()
			if not plan_code:
				continue
			candidates = {
				plan_code.lower(),
				str(plan.get("plan_name") or "").strip().lower(),
				str(plan.get("name") or "").strip().lower(),
			}
			if normalized_bundle not in candidates and all(normalized_bundle not in candidate for candidate in candidates if candidate):
				continue

			amount_value = plan.get("amount")
			if amount_value is None:
				amount_value = plan.get("price")
			if amount_value is None:
				amount_value = plan.get("plan_amount")
			if amount_value is None:
				amount_value = plan.get("cost")
			if amount_value is None:
				raise ProviderUnavailableError("VTUGate data plan amount is missing.")

			return plan_code, str(amount_value)

		raise ProviderUnavailableError(f"VTUGate data plan not found for '{bundle_code}'.")

	async def fetch_account_details(self) -> dict[str, Any]:
		return await self._request(path="/api/v1/accountdetails", data={})

	async def fetch_balance(self) -> dict[str, Any]:
		account = await self.fetch_account_details()
		wallet_balance = None
		if isinstance(account.get("data"), dict):
			wallet_balance = account["data"].get("wallet_balance")
		return {
			"status": account.get("status"),
			"message": account.get("message"),
			"data": {
				"wallet_balance": wallet_balance,
			},
		}

	async def check_balance(self) -> dict[str, Any]:
		return await self.fetch_balance()

	async def fetch_services(self, *, service_type: str) -> dict[str, Any]:
		return await self._request(path="/api/v1/fetchservices", data={"service_type": service_type})

	async def buy_airtime(
		self,
		*,
		phone_number: str,
		network: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		service_id = await self._resolve_service_id(service_type="airtime", value=network)
		return await self._request(
			path="/api/v1/buyairtime",
			data={
				"service_id": service_id,
				"phone_number": phone_number,
				"amount": amount,
			},
		)

	async def fetch_data_plans(self, *, network: str) -> dict[str, Any]:
		service_id = await self._resolve_service_id(service_type="data", value=network)
		return await self._request(path="/api/v1/fetchdataplans", data={"service_id": service_id})

	async def buy_data(
		self,
		*,
		phone_number: str,
		network: str,
		bundle_code: str,
		reference: str | None = None,
	) -> dict[str, Any]:
		service_id = await self._resolve_service_id(service_type="data", value=network)
		plans = await self._request(path="/api/v1/fetchdataplans", data={"service_id": service_id})
		plan_code, amount = self._extract_data_plan(plans_payload=plans, bundle_code=bundle_code)
		return await self._request(
			path="/api/v1/buydata",
			data={
				"service_id": service_id,
				"phone_number": phone_number,
				"amount": amount,
				"plan_code": plan_code,
			},
		)

	async def verify_electricity(
		self,
		*,
		meter_number: str,
		provider: str,
	) -> dict[str, Any]:
		service_id = await self._resolve_service_id(service_type="electricity", value=provider)
		return await self._request(
			path="/api/v1/verifyelectricity",
			data={
				"service_id": service_id,
				"meter_no": meter_number,
				"disco": provider,
			},
		)

	async def fetch_electricity_providers(self) -> dict[str, Any]:
		return await self.fetch_services(service_type="electricity")

	async def purchase_electricity(
		self,
		*,
		meter_number: str,
		provider: str,
		amount: float | int,
		reference: str | None = None,
		phone_number: str | None = None,
	) -> dict[str, Any]:
		if phone_number is None or not str(phone_number).strip():
			raise ProviderUnavailableError("VTUGate phone_number is required for electricity purchase.")
		service_id = await self._resolve_service_id(service_type="electricity", value=provider)
		return await self._request(
			path="/api/v1/buyelectricity",
			data={
				"service_id": service_id,
				"meter_no": meter_number,
				"disco": provider,
				"amount": amount,
				"phone_number": phone_number,
			},
		)

	async def verify_cable_tv(
		self,
		*,
		smart_card_number: str,
		provider_code: str,
		phone: str,
	) -> dict[str, Any]:
		service_id = await self._resolve_service_id(service_type="tv", value=provider_code)
		return await self._request(
			path="/api/v1/verifycabletv",
			data={
				"service_id": service_id,
				"phone": phone,
				"smartcard_number": smart_card_number,
			},
		)

	async def fetch_cable_tv_providers(self) -> dict[str, Any]:
		return await self.fetch_services(service_type="tv")

	async def fetch_cable_tv_bouquets(self, *, provider_code: str) -> dict[str, Any]:
		raise NotImplementedError("VTUGate cable TV bouquet integration is not implemented yet.")

	async def subscribe_tv(
		self,
		*,
		smart_card_number: str,
		provider_code: str,
		package: str,
		package_code: str,
		amount: float | int,
		reference: str | None = None,
		phone: str | None = None,
	) -> dict[str, Any]:
		if phone is None or not str(phone).strip():
			raise ProviderUnavailableError("VTUGate phone is required for cable TV subscription.")
		service_id = await self._resolve_service_id(service_type="tv", value=provider_code)
		return await self._request(
			path="/api/v1/buycabletv",
			data={
				"service_id": service_id,
				"phone": phone,
				"smartcard_number": smart_card_number,
				"amount": amount,
				"plan_code": package_code,
				"plan_name": package,
			},
		)

	async def get_education_price(self, *, service_id: str | int) -> dict[str, Any]:
		return await self._request(path="/api/v1/geteducationtypeprice", data={"service_id": service_id})

	async def buy_education_pins(
		self,
		*,
		service_id: str | int,
		phone: str,
		quantity: int,
		product_code: str,
	) -> dict[str, Any]:
		return await self._request(
			path="/api/v1/buyeducation",
			data={
				"service_id": service_id,
				"phone": phone,
				"quantity": quantity,
				"product_code": product_code,
			},
		)

	async def verify_transaction(self, *, provider_reference: str | None = None) -> dict[str, Any]:
		reference = (provider_reference or "").strip()
		if not reference:
			raise ProviderUnavailableError("VTUGate transaction reference is required for verification.")
		return await self._request(
			path="/api/v1/transactionstatus",
			data={
				"external_reference": reference,
				"requery": "true",
			},
		)

	async def health_check(self) -> dict[str, Any]:
		return await self.fetch_account_details()
