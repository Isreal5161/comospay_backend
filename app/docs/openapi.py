from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def custom_openapi_schema(app: FastAPI, *, api_version: str = "v1") -> dict[str, Any]:
    """Return a customized OpenAPI schema for the FastAPI application."""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="CosmozPay API",
        version=api_version,
        description=(
            "CosmozPay is a secure, enterprise-grade fintech platform for wallets, payments, "
            "bill payments, and digital services."
        ),
        routes=app.routes,
        openapi_version="3.1.0",
        contact={
            "name": "CosmozPay Support",
            "email": "support@cosmozpay.com",
            "url": "https://cosmozpay.com",
        },
        license_info={
            "name": "MIT License",
            "url": "https://opensource.org/licenses/MIT",
        },
        terms_of_service="https://cosmozpay.com/terms",
    )

    openapi_schema.setdefault("tags", [])
    openapi_schema.setdefault("securitySchemes", {})
    openapi_schema.setdefault("components", {}).setdefault("schemas", {})
    openapi_schema.setdefault("components", {}).setdefault("responses", {})

    openapi_schema["tags"] = [
        {"name": "Authentication", "description": "Authentication and identity flows."},
        {"name": "Users", "description": "User profile and account management."},
        {"name": "Wallet", "description": "Wallet funding, transfers, and balances."},
        {"name": "Payments", "description": "Payment initiation and processing."},
        {"name": "Transactions", "description": "Transaction history and status retrieval."},
        {"name": "Airtime", "description": "Airtime purchase operations."},
        {"name": "Data", "description": "Data bundle purchase operations."},
        {"name": "Electricity", "description": "Electricity bill payments."},
        {"name": "Cable TV", "description": "Cable TV bill payments."},
        {"name": "Education", "description": "Education-related services."},
        {"name": "Gift Cards", "description": "Gift card purchase and redemption."},
        {"name": "Notifications", "description": "Notification delivery and preferences."},
        {"name": "Admin", "description": "Administrative operations and management tools."},
    ]

    openapi_schema["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "JWT access token used for authenticated API requests.",
    }

    openapi_schema["security"] = [{"BearerAuth": []}]

    openapi_schema["components"]["responses"].update(
        {
            "SuccessResponse": {
                "description": "Successful operation.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean", "example": True},
                                "message": {"type": "string", "example": "Success"},
                                "data": {},
                                "status_code": {"type": "integer", "example": 200},
                            },
                        }
                    }
                },
            },
            "ValidationErrorResponse": {
                "description": "Validation failed.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean", "example": False},
                                "message": {"type": "string", "example": "Validation failed"},
                                "errors": {},
                                "status_code": {"type": "integer", "example": 422},
                            },
                        }
                    }
                },
            },
            "UnauthorizedResponse": {
                "description": "Authentication is required or the token is invalid.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean", "example": False},
                                "message": {"type": "string", "example": "Authentication token is required."},
                                "errors": {},
                                "status_code": {"type": "integer", "example": 401},
                            },
                        }
                    }
                },
            },
            "ForbiddenResponse": {
                "description": "The caller is authenticated but not allowed to access the resource.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean", "example": False},
                                "message": {"type": "string", "example": "You are not authorized to access this resource."},
                                "errors": {},
                                "status_code": {"type": "integer", "example": 403},
                            },
                        }
                    }
                },
            },
            "NotFoundResponse": {
                "description": "The requested resource was not found.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean", "example": False},
                                "message": {"type": "string", "example": "Resource not found."},
                                "errors": {},
                                "status_code": {"type": "integer", "example": 404},
                            },
                        }
                    }
                },
            },
            "RateLimitedResponse": {
                "description": "Too many requests were made within the allowed window.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean", "example": False},
                                "message": {"type": "string", "example": "Too many requests. Please retry later."},
                                "errors": {},
                                "status_code": {"type": "integer", "example": 429},
                            },
                        }
                    }
                },
            },
            "InternalServerErrorResponse": {
                "description": "An unexpected server error occurred.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean", "example": False},
                                "message": {"type": "string", "example": "An unexpected error occurred."},
                                "errors": {},
                                "status_code": {"type": "integer", "example": 500},
                            },
                        }
                    }
                },
            },
        }
    )

    app.openapi_schema = openapi_schema
    return app.openapi_schema


def configure_openapi(app: FastAPI, *, api_version: str = "v1") -> FastAPI:
    """Attach the customized OpenAPI schema generator to a FastAPI app."""
    app.openapi = lambda: custom_openapi_schema(app, api_version=api_version)
    return app
