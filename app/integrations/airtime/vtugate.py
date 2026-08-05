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


class VTUGateProvider(VTUProvider):
	"""VTUGate VTU provider contract implementation."""

	def __init__(self, *, api_key: SecretStr | str | None = None, base_url: str | None = None) -> None:
		self._api_key = _resolve_secret(api_key if api_key is not None else settings.vtugate_api_key)
		self._base_url = (base_url if base_url is not None else settings.vtugate_base_url) or ""

	@property
	def name(self) -> str:
		return "vtugate"

	@property
	def priority(self) -> int:
		return 20

	@property
	def is_available(self) -> bool:
		return bool(self._api_key)

	async def check_balance(self) -> dict[str, Any]:
		raise NotImplementedError("VTUGate balance integration is not implemented yet.")

	async def buy_airtime(
		self,
		*,
		phone_number: str,
		network: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		raise NotImplementedError("VTUGate airtime purchase is not implemented yet.")

	async def buy_data(
		self,
		*,
		phone_number: str,
		network: str,
		bundle_code: str,
		reference: str | None = None,
	) -> dict[str, Any]:
		raise NotImplementedError("VTUGate data purchase is not implemented yet.")

	async def purchase_electricity(
		self,
		*,
		meter_number: str,
		provider: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		raise NotImplementedError("VTUGate electricity purchase is not implemented yet.")

	async def subscribe_tv(
		self,
		*,
		smart_card_number: str,
		package: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		raise NotImplementedError("VTUGate TV subscription is not implemented yet.")

	async def verify_transaction(self, *, provider_reference: str | None = None) -> dict[str, Any]:
		raise NotImplementedError("VTUGate transaction verification is not implemented yet.")

	async def health_check(self) -> dict[str, Any]:
		raise NotImplementedError("VTUGate health check integration is not implemented yet.")
