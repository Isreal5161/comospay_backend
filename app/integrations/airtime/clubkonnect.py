from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from app.config.settings import settings
from app.integrations.airtime.base_provider import VTUProvider


def _resolve_secret(value: SecretStr | str | None) -> str | None:
	if value is None:
		return None
	if isinstance(value, SecretStr):
		raw = value.get_secret_value()
	else:
		raw = value
	normalized = raw.strip()
	return normalized or None


class ClubConnectProvider(VTUProvider):
	"""ClubConnect VTU provider contract implementation."""

	def __init__(self, *, api_key: SecretStr | str | None = None, base_url: str | None = None) -> None:
		default_key = settings.clubconnect_api_key if settings.clubconnect_api_key is not None else settings.clubkonnect_api_key
		self._api_key = _resolve_secret(api_key if api_key is not None else default_key)
		self._base_url = (base_url if base_url is not None else settings.clubconnect_base_url) or ""

	@property
	def name(self) -> str:
		return "clubconnect"

	@property
	def priority(self) -> int:
		return 40

	@property
	def is_available(self) -> bool:
		return bool(self._api_key)

	async def check_balance(self) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect balance integration is not implemented yet.")

	async def fetch_data_plans(self, *, network: str) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect data plans integration is not implemented yet.")

	async def fetch_electricity_providers(self) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect electricity providers integration is not implemented yet.")

	async def verify_electricity(self, *, meter_number: str, provider: str) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect electricity verification is not implemented yet.")

	async def fetch_cable_tv_providers(self) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect cable TV providers integration is not implemented yet.")

	async def fetch_cable_tv_bouquets(self, *, provider_code: str) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect cable TV bouquets integration is not implemented yet.")

	async def verify_cable_tv(self, *, smart_card_number: str, provider_code: str, phone: str) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect cable TV verification is not implemented yet.")

	async def get_education_price(self, *, service_id: str | int) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect education pricing is not implemented yet.")

	async def buy_education_pins(
		self,
		*,
		service_id: str | int,
		phone: str,
		quantity: int,
		product_code: str,
	) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect education pin purchase is not implemented yet.")

	async def buy_airtime(
		self,
		*,
		phone_number: str,
		network: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect airtime purchase is not implemented yet.")

	async def buy_data(
		self,
		*,
		phone_number: str,
		network: str,
		bundle_code: str,
		reference: str | None = None,
	) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect data purchase is not implemented yet.")

	async def purchase_electricity(
		self,
		*,
		meter_number: str,
		provider: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect electricity purchase is not implemented yet.")

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
		raise NotImplementedError("ClubConnect TV subscription is not implemented yet.")

	async def verify_transaction(self, *, provider_reference: str | None = None) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect transaction verification is not implemented yet.")

	async def health_check(self) -> dict[str, Any]:
		raise NotImplementedError("ClubConnect health check integration is not implemented yet.")
