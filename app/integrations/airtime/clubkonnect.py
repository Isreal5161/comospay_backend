from __future__ import annotations

import logging
from datetime import UTC, datetime
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


def _normalize_base_url(value: str | None) -> str | None:
	if value is None:
		return None
	normalized = value.strip().rstrip("/")
	return normalized or None


class ClubConnectProvider(VTUProvider):
	"""ClubConnect VTU provider contract implementation."""

	_EDUCATION_PACKAGE_ENDPOINTS: dict[str, str] = {
		"waec": "APIWAECPackagesV2.asp",
		"jamb": "APIJAMBPackagesV2.asp",
	}

	_EDUCATION_PURCHASE_ENDPOINTS: dict[str, str] = {
		"waec": "APIWAECV1.asp",
		"jamb": "APIJAMBV1.asp",
	}

	def __init__(
		self,
		*,
		api_key: SecretStr | str | None = None,
		base_url: str | None = None,
		timeout: float | None = None,
		logger: logging.Logger | None = None,
	) -> None:
		default_key = settings.clubconnect_api_key if settings.clubconnect_api_key is not None else settings.clubkonnect_api_key
		default_user_id = settings.clubconnect_user_id if settings.clubconnect_user_id is not None else settings.clubkonnect_user_id
		default_callback_url = (
			settings.clubconnect_callback_url if settings.clubconnect_callback_url is not None else settings.clubkonnect_callback_url
		)
		default_phone_no = settings.clubconnect_phone_no if settings.clubconnect_phone_no is not None else settings.clubkonnect_phone_no
		default_meter_type = (
			settings.clubconnect_meter_type if settings.clubconnect_meter_type.strip() else settings.clubkonnect_meter_type
		)
		self._api_key = _resolve_secret(api_key if api_key is not None else default_key)
		self._base_url = _normalize_base_url(base_url if base_url is not None else settings.clubconnect_base_url) or ""
		self._user_id = (default_user_id or "").strip() or None
		self._callback_url = (default_callback_url or "").strip() or None
		self._default_phone_no = (default_phone_no or "").strip() or None
		self._default_meter_type = (default_meter_type or "").strip() or "01"
		self._timeout = timeout if timeout is not None else self._resolve_timeout()
		self.logger = logger or logging.getLogger(__name__)

	@property
	def name(self) -> str:
		return "clubconnect"

	@property
	def priority(self) -> int:
		return 40

	@property
	def is_available(self) -> bool:
		return bool(self._api_key and self._user_id and self._base_url)

	async def check_balance(self) -> dict[str, Any]:
		payload = await self._request(path="APIWalletBalanceV1.asp", params=self._auth_params())
		return payload

	async def fetch_data_plans(self, *, network: str) -> dict[str, Any]:
		payload = await self._request(path="APIDatabundlePlansV2.asp", params=self._user_params())
		return payload

	async def fetch_electricity_providers(self) -> dict[str, Any]:
		payload = await self._request(path="APIElectricityTypeV2.asp", params=self._user_params())
		return payload

	async def verify_electricity(self, *, meter_number: str, provider: str) -> dict[str, Any]:
		payload = await self._request(
			path="APIVerifyElectricityV1.asp",
			params={
				**self._auth_params(),
				"ElectricCompany": provider,
				"MeterNo": meter_number,
				"MeterType": self._default_meter_type,
			},
		)
		return payload

	async def fetch_cable_tv_providers(self) -> dict[str, Any]:
		payload = await self._request(path="APICableTVTypeV2.asp", params=self._user_params())
		return payload

	async def fetch_cable_tv_bouquets(self, *, provider_code: str) -> dict[str, Any]:
		payload = await self._request(path="APICableTVPackagesV2.asp", params=self._user_params())
		return payload

	async def verify_cable_tv(self, *, smart_card_number: str, provider_code: str, phone: str) -> dict[str, Any]:
		payload = await self._request(
			path="APIVerifyCableTVV1.asp",
			params={
				**self._auth_params(),
				"CableTV": provider_code,
				"SmartCardNo": smart_card_number,
			},
		)
		return payload

	async def get_education_price(self, *, service_id: str | int) -> dict[str, Any]:
		service_key = str(service_id).strip().lower()
		endpoint = self._EDUCATION_PACKAGE_ENDPOINTS.get(service_key)
		if endpoint is None:
			raise ProviderUnavailableError("ClubConnect education service_id must be explicitly set to 'waec' or 'jamb'.")
		payload = await self._request(path=endpoint, params=self._user_params())
		return payload

	async def buy_education_pins(
		self,
		*,
		service_id: str | int,
		phone: str,
		quantity: int,
		product_code: str,
	) -> dict[str, Any]:
		callback_url = self._require_callback_url()
		service_key = str(service_id).strip().lower()
		endpoint = self._EDUCATION_PURCHASE_ENDPOINTS.get(service_key)
		if endpoint is None:
			raise ProviderUnavailableError("ClubConnect education service_id must be explicitly set to 'waec' or 'jamb'.")

		exam_type = str(product_code).strip()
		if not exam_type:
			raise ProviderUnavailableError("ClubConnect ExamType is required for education pin purchase.")

		payload = await self._request(
			path=endpoint,
			params={
				**self._auth_params(),
				"ExamType": exam_type,
				"PhoneNo": phone,
				"RequestID": self._build_request_id(prefix="EDU"),
				"CallBackURL": callback_url,
			},
		)
		return payload

	async def buy_airtime(
		self,
		*,
		phone_number: str,
		network: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		payload = await self._request(
			path="APIAirtimeV1.asp",
			params={
				**self._auth_params(),
				"MobileNetwork": network,
				"Amount": amount,
				"MobileNumber": phone_number,
				"RequestID": self._build_request_id(reference=reference, prefix="AIR"),
				"CallBackURL": self._require_callback_url(),
			},
		)
		return payload

	async def buy_data(
		self,
		*,
		phone_number: str,
		network: str,
		bundle_code: str,
		reference: str | None = None,
	) -> dict[str, Any]:
		payload = await self._request(
			path="APIDatabundleV1.asp",
			params={
				**self._auth_params(),
				"MobileNetwork": network,
				"DataPlan": bundle_code,
				"MobileNumber": phone_number,
				"RequestID": self._build_request_id(reference=reference, prefix="DAT"),
				"CallBackURL": self._require_callback_url(),
			},
		)
		return payload

	async def purchase_electricity(
		self,
		*,
		meter_number: str,
		provider: str,
		amount: float | int,
		reference: str | None = None,
	) -> dict[str, Any]:
		payload = await self._request(
			path="APIElectricityV1.asp",
			params={
				**self._auth_params(),
				"ElectricCompany": provider,
				"MeterType": self._default_meter_type,
				"MeterNo": meter_number,
				"Amount": amount,
				"PhoneNo": self._require_phone_no(),
				"RequestID": self._build_request_id(reference=reference, prefix="ELE"),
				"CallBackURL": self._require_callback_url(),
			},
		)
		return payload

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
		payload = await self._request(
			path="APICableTVV1.asp",
			params={
				**self._auth_params(),
				"CableTV": provider_code,
				"Package": package_code or package,
				"SmartCardNo": smart_card_number,
				"PhoneNo": self._require_phone_no(),
				"RequestID": self._build_request_id(reference=reference, prefix="CAB"),
				"CallBackURL": self._require_callback_url(),
			},
		)
		return payload

	async def verify_transaction(self, *, provider_reference: str | None = None) -> dict[str, Any]:
		reference = (provider_reference or "").strip()
		if not reference:
			raise ProviderUnavailableError("ClubConnect transaction reference is required for verification.")

		payload = await self._request(
			path="APIQueryV1.asp",
			params={
				**self._auth_params(),
				"OrderID": reference,
			},
			raise_on_business_failure=False,
		)
		payload_dict = self._ensure_dict(payload)
		status_code = str(payload_dict.get("statuscode") or "").strip()
		status_text = str(payload_dict.get("status") or payload_dict.get("orderstatus") or "").strip().upper()

		if self._looks_like_query_reference_error(payload_dict):
			payload = await self._request(
				path="APIQueryV1.asp",
				params={
					**self._auth_params(),
					"RequestID": reference,
				},
				raise_on_business_failure=False,
			)
			payload_dict = self._ensure_dict(payload)
			status_code = str(payload_dict.get("statuscode") or "").strip()
			status_text = str(payload_dict.get("status") or payload_dict.get("orderstatus") or "").strip().upper()

		if self._is_temporary_status(status=status_text, status_code=status_code):
			message = self._extract_message(payload_dict, fallback="ClubConnect transaction is pending provider retry.")
			raise ProviderTemporaryFailure(message)

		if self._is_permanent_status(status=status_text, status_code=status_code, payload=payload_dict):
			message = self._extract_message(payload_dict, fallback="ClubConnect transaction verification failed.")
			raise ProviderUnavailableError(message)

		return payload_dict

	async def health_check(self) -> dict[str, Any]:
		return await self.check_balance()

	def _resolve_timeout(self) -> float:
		connect_timeout = getattr(settings, "connection_timeout", 10)
		read_timeout = getattr(settings, "read_timeout", 10)
		write_timeout = getattr(settings, "write_timeout", 10)
		return float(max(connect_timeout or 0, read_timeout or 0, write_timeout or 0, 1))

	def _headers(self) -> dict[str, str]:
		if not self._base_url:
			raise ProviderUnavailableError("ClubConnect base URL is not configured.")
		return {
			"Accept": "application/json",
		}

	def _auth_params(self) -> dict[str, str]:
		if not self._user_id:
			raise ProviderUnavailableError("ClubConnect UserID is not configured.")
		if not self._api_key:
			raise ProviderUnavailableError("ClubConnect APIKey is not configured.")
		if not self._base_url:
			raise ProviderUnavailableError("ClubConnect base URL is not configured.")
		return {
			"UserID": self._user_id,
			"APIKey": self._api_key,
		}

	def _user_params(self) -> dict[str, str]:
		if not self._user_id:
			raise ProviderUnavailableError("ClubConnect UserID is not configured.")
		if not self._base_url:
			raise ProviderUnavailableError("ClubConnect base URL is not configured.")
		return {"UserID": self._user_id}

	def _require_callback_url(self) -> str:
		if not self._callback_url:
			raise ProviderUnavailableError("ClubConnect CallBackURL is not configured.")
		return self._callback_url

	def _require_phone_no(self) -> str:
		if not self._default_phone_no:
			raise ProviderUnavailableError("ClubConnect PhoneNo is not configured.")
		return self._default_phone_no

	def _build_request_id(self, *, reference: str | None = None, prefix: str = "CK") -> str:
		normalized = (reference or "").strip()
		if normalized:
			return normalized
		return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"

	def _extract_message(self, payload: dict[str, Any], *, fallback: str) -> str:
		for key in ("description", "remark", "message", "status", "orderstatus"):
			value = payload.get(key)
			if isinstance(value, str) and value.strip():
				return value.strip()
		return fallback

	def _is_temporary_message(self, message: str) -> bool:
		normalized = message.strip().lower()
		if not normalized:
			return False
		keywords = {
			"network error",
			"server busy",
			"cannot be processed",
			"cannot process",
			"network unresponsive",
			"on hold",
			"timeout",
			"temporar",
			"try again",
			"awaiting",
		}
		return any(keyword in normalized for keyword in keywords)

	def _is_permanent_message(self, message: str) -> bool:
		normalized = message.strip().upper()
		if not normalized:
			return False
		markers = {
			"INVALID_CREDENTIALS",
			"MISSING_APIKEY",
			"MISSING_USERID",
			"INVALID_AMOUNT",
			"INVALID_RECIPIENT",
			"INVALID_DATAPLAN",
			"INVALID_SMARTCARDNO",
			"INVALID_METERNO",
			"INVALID_CUSTOMERID",
			"PACKAGE_NOT_AVAILABLE",
			"DATAPLAN_NOT_AVAILABLE",
			"INSUFFICIENT_BALANCE",
		}
		return any(marker in normalized for marker in markers)

	def _is_temporary_status(self, *, status: str, status_code: str) -> bool:
		status_upper = status.strip().upper()
		if status_upper in {"ORDER_ONHOLD", "ORDER_PROCESSED"}:
			return True
		if status_code in {"201", "300", "600", "601", "603", "604", "605", "606", "699"}:
			return True
		return False

	def _is_permanent_status(self, *, status: str, status_code: str, payload: dict[str, Any]) -> bool:
		status_upper = status.strip().upper()
		if status_upper in {"ORDER_ERROR", "ORDER_CANCELLED"}:
			return True
		if status_code.isdigit() and int(status_code) >= 400 and int(status_code) < 600:
			return True
		customer_name = payload.get("customer_name")
		if isinstance(customer_name, str) and customer_name.strip().upper().startswith("INVALID_"):
			return True
		return False

	def _looks_like_query_reference_error(self, payload: dict[str, Any]) -> bool:
		message = self._extract_message(payload, fallback="")
		if not message:
			return False
		normalized = message.upper()
		return "MISSING_CREDENTIALS" in normalized or "INVALID_" in normalized or "MISSING_" in normalized

	def _ensure_dict(self, payload: Any) -> dict[str, Any]:
		if not isinstance(payload, dict):
			raise ProviderUnavailableError("ClubConnect returned an invalid response payload.")
		return payload

	async def _request(
		self,
		*,
		path: str,
		params: dict[str, Any],
		raise_on_business_failure: bool = True,
	) -> Any:
		if not self._base_url:
			raise ProviderUnavailableError("ClubConnect base URL is not configured.")

		url = f"{self._base_url}/{path.lstrip('/')}"
		headers = self._headers()
		clean_params = {key: str(value) for key, value in params.items() if value is not None}

		try:
			async with httpx.AsyncClient(timeout=self._timeout) as client:
				response = await client.get(url=url, params=clean_params, headers=headers)
		except httpx.TimeoutException as exc:
			self.logger.warning("clubconnect_request_timeout", extra={"path": path, "error": str(exc)})
			raise ProviderTemporaryFailure("ClubConnect request timed out.") from exc
		except httpx.RequestError as exc:
			self.logger.warning("clubconnect_connection_error", extra={"path": path, "error": str(exc)})
			raise ProviderTemporaryFailure("ClubConnect connection failure.") from exc

		try:
			payload = response.json()
		except ValueError as exc:
			self.logger.warning("clubconnect_invalid_json", extra={"path": path, "status_code": response.status_code, "error": str(exc)})
			raise ProviderUnavailableError("ClubConnect returned malformed JSON.") from exc

		if response.status_code >= 500:
			message = "ClubConnect is temporarily unavailable."
			if isinstance(payload, dict):
				message = self._extract_message(payload, fallback=message)
			raise ProviderTemporaryFailure(message)

		if response.status_code >= 400:
			message = "ClubConnect request failed."
			if isinstance(payload, dict):
				message = self._extract_message(payload, fallback=message)
			if self._is_temporary_message(message):
				raise ProviderTemporaryFailure(message)
			raise ProviderUnavailableError(message)

		if raise_on_business_failure and isinstance(payload, dict):
			message = self._extract_message(payload, fallback="ClubConnect request failed.")
			status = str(payload.get("status") or payload.get("orderstatus") or "").strip().upper()
			status_code = str(payload.get("statuscode") or "").strip()

			if self._is_permanent_message(message) or self._is_permanent_status(status=status, status_code=status_code, payload=payload):
				raise ProviderUnavailableError(message)
			if self._is_temporary_message(message) or self._is_temporary_status(status=status, status_code=status_code):
				raise ProviderTemporaryFailure(message)

		return self._ensure_dict(payload)
