from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AdminBase(BaseModel):
    """Shared admin profile fields for API validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    email: EmailStr | None = Field(default=None, description="Admin email address.")
    username: str | None = Field(default=None, min_length=3, max_length=100, description="Admin username.")
    role: str | None = Field(default=None, max_length=50, description="Admin role.")
    status: str | None = Field(default=None, max_length=50, description="Admin account status.")
    is_active: bool | None = Field(default=None, description="Whether the admin account is active.")
    first_name: str | None = Field(default=None, max_length=100, description="Admin first name.")
    last_name: str | None = Field(default=None, max_length=100, description="Admin last name.")
    phone: str | None = Field(default=None, max_length=20, description="Admin phone number.")


class AdminCreate(AdminBase):
    """Schema for creating a new admin account."""

    email: EmailStr = Field(..., description="Admin email address.")
    username: str = Field(..., min_length=3, max_length=100, description="Admin username.")
    password: str = Field(..., min_length=8, max_length=255, description="Raw admin password.")
    role: str | None = Field(default="admin", max_length=50, description="Admin role.")
    first_name: str | None = Field(default=None, max_length=100, description="Admin first name.")
    last_name: str | None = Field(default=None, max_length=100, description="Admin last name.")
    phone: str | None = Field(default=None, max_length=20, description="Admin phone number.")


class AdminUpdate(BaseModel):
    """Schema for updating admin profile and account metadata."""

    model_config = ConfigDict(from_attributes=True)

    email: EmailStr | None = Field(default=None, description="Admin email address.")
    username: str | None = Field(default=None, min_length=3, max_length=100, description="Admin username.")
    first_name: str | None = Field(default=None, max_length=100, description="Admin first name.")
    last_name: str | None = Field(default=None, max_length=100, description="Admin last name.")
    phone: str | None = Field(default=None, max_length=20, description="Admin phone number.")
    role: str | None = Field(default=None, max_length=50, description="Admin role.")
    status: str | None = Field(default=None, max_length=50, description="Admin account status.")
    is_active: bool | None = Field(default=None, description="Whether the admin account is active.")


class AdminResponse(AdminBase):
    """Safe public response schema for admin records."""

    id: UUID = Field(..., description="Admin identifier.")
    created_at: datetime = Field(..., description="Admin creation timestamp.")
    updated_at: datetime = Field(..., description="Admin update timestamp.")


class AdminLoginSchema(BaseModel):
    """Schema for admin authentication requests."""

    login_identifier: str = Field(..., min_length=3, max_length=255, description="Email or username used for login.")
    password: str = Field(..., min_length=8, max_length=255, description="Admin password.")


class AdminPasswordChangeSchema(BaseModel):
    """Schema for admin password change requests."""

    current_password: str = Field(..., min_length=8, max_length=255, description="Current password.")
    new_password: str = Field(..., min_length=8, max_length=255, description="New password.")


class AdminUserResponse(BaseModel):
    """Safe user fields exposed to authorized administrators."""

    id: UUID
    first_name: str | None
    last_name: str | None
    email: str
    username: str | None
    phone: str | None
    status: str
    is_active: bool
    is_blocked: bool
    is_suspended: bool
    is_email_verified: bool
    is_phone_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminUserListData(BaseModel):
    """Filtered, paginated admin user listing."""

    items: list[AdminUserResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class AdminUserListResponse(BaseModel):
    """Standard API envelope for admin user listings."""

    success: bool
    message: str
    data: AdminUserListData
    status_code: int


class AdminUserDetailResponse(BaseModel):
    """Standard API envelope for an admin user profile."""

    success: bool
    message: str
    data: AdminUserResponse
    status_code: int


class AdminUserManagementData(BaseModel):
    user: AdminUserResponse
    action: str


class AdminUserManagementResponse(BaseModel):
    """Standard API envelope for account-state management."""

    success: bool
    message: str
    data: AdminUserManagementData
    status_code: int


class DashboardPeriod(BaseModel):
    """Half-open reporting window in the Africa/Lagos timezone."""

    start: datetime
    end: datetime


class DashboardMetricComparison(BaseModel):
    """Current and equivalent prior-period values without inferred percentages."""

    current: int
    previous: int | None
    change: int | None
    current_period: DashboardPeriod | None
    previous_period: DashboardPeriod | None
    unavailable_reason: str | None = None


class DashboardMetrics(BaseModel):
    total_users: int
    active_users_today: int
    new_registrations_today: int
    total_transactions: int = Field(description="All persisted transactions, regardless of lifecycle status.")
    pending_transactions: int = Field(description="Transactions whose current status is exactly 'pending'.")
    failed_transactions: int = Field(description="Transactions whose current status is exactly 'failed'.")
    period_comparison: dict[str, DashboardMetricComparison] = Field(
        description="Absolute equivalent-period comparisons; no percentage changes are inferred."
    )


class DashboardMoneyByCurrency(BaseModel):
    currency: str
    amount: str


class DashboardWalletBalances(BaseModel):
    currency: str
    ledger_balance: str
    available_balance: str
    locked_balance: str


class DashboardAnalyticsBucket(BaseModel):
    """Bucketed counts and successful monetary volume, grouped by currency."""

    bucket: str
    registrations: int
    transactions: int
    transaction_volume: list[DashboardMoneyByCurrency]


class DashboardFinancialMetrics(BaseModel):
    revenue: str | None
    profit: str | None
    revenue_unavailable_reason: str | None
    profit_unavailable_reason: str | None


class DashboardCategoryBreakdown(BaseModel):
    category: str
    provider: str | None
    currency: str
    transaction_count: int
    transaction_volume: str


class DashboardData(BaseModel):
    metrics: DashboardMetrics
    wallet_balances: list[DashboardWalletBalances]
    transaction_volume: list[DashboardMoneyByCurrency] = Field(
        description=(
            "All-time sum of transactions in successful lifecycle states, grouped by currency. "
            "The states are completed, posted, successful, succeeded (case-insensitive), and settled."
        )
    )
    financial_metrics: DashboardFinancialMetrics
    analytics: dict[str, list[DashboardAnalyticsBucket]] = Field(
        description=(
            "Daily contains the last 7 Lagos calendar days, weekly the last 12 Monday-based weeks, "
            "and monthly the last 12 calendar months."
        )
    )
    category_breakdown: list[DashboardCategoryBreakdown] = Field(
        description=(
            "Raw category/provider transaction counts and successful transaction volume by currency; "
            "transaction counts include every lifecycle status."
        )
    )


class DashboardResponse(BaseModel):
    """Standard controller envelope for the admin dashboard endpoint."""

    success: bool
    message: str
    data: DashboardData
    status_code: int
