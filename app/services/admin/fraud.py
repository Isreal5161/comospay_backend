from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, String, case, cast, func, literal, or_, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import Select

from app.models.audit_log import AuditLog
from app.models.device import Device
from app.models.fraud_investigation import FraudInvestigation, FraudInvestigationEvent
from app.models.transaction import Transaction
from app.models.user import User
from app.utils.exceptions import ValidationException


_HIGH_VALUE = Decimal("50000")
_RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
_TRANSITIONS = {
    ("open", "review"): "in_review",
    ("open", "dismiss"): "dismissed",
    ("open", "escalate"): "escalated",
    ("in_review", "dismiss"): "dismissed",
    ("in_review", "escalate"): "escalated",
    ("in_review", "resolve"): "resolved",
    ("escalated", "review"): "in_review",
    ("escalated", "dismiss"): "dismissed",
    ("escalated", "resolve"): "resolved",
}


class FraudService:
    """Monitor persisted risk signals and persist administrator investigation decisions."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        user_repository: Any | None = None,
        transaction_repository: Any | None = None,
        wallet_repository: Any | None = None,
        audit_repository: Any | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.user_repository = user_repository
        self.transaction_repository = transaction_repository
        self.wallet_repository = wallet_repository
        self.audit_repository = audit_repository

    async def list_fraud(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        status: str | None = None,
        risk_level: str | None = None,
        event_type: str | None = None,
        admin_id: UUID | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        if page < 1 or page > 1_000_000 or page_size < 1 or page_size > 100:
            raise ValidationException("page must be between 1 and 1,000,000 and page_size between 1 and 100.")
        if risk_level and risk_level.strip().upper() not in _RISK_LEVELS:
            raise ValidationException("risk_level must be LOW, MEDIUM, HIGH, or CRITICAL.")

        session = self._session()
        filtered = self._filtered_signal_query(
            search=search,
            status=status,
            risk_level=risk_level,
            event_type=event_type,
        ).subquery("filtered_fraud_signals")

        aggregate = (
            await session.execute(
                select(
                    func.count().label("total"),
                    func.sum(case((filtered.c.signal_type == "transaction", 1), else_=0)).label("transactions"),
                    func.sum(
                        case(
                            (
                                (filtered.c.signal_type == "account_security")
                                & (filtered.c.failed_attempts > 0),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("accounts_with_failed_logins"),
                    func.sum(case((filtered.c.signal_type == "account_security", 1), else_=0)).label(
                        "account_security_signals"
                    ),
                    func.count(
                        func.distinct(
                            case(
                                (filtered.c.risk_level.in_(("HIGH", "CRITICAL")), filtered.c.user_id),
                                else_=None,
                            )
                        )
                    ).label("high_risk_accounts"),
                ).select_from(filtered)
            )
        ).one()
        risk_counts_result = await session.execute(
            select(filtered.c.risk_level, func.count())
            .group_by(filtered.c.risk_level)
        )
        risk_flags = {level: 0 for level in _RISK_LEVELS}
        for level, count in risk_counts_result.all():
            if level in risk_flags:
                risk_flags[level] = int(count)

        total = int(aggregate.total or 0)
        statement = (
            select(
                filtered,
                FraudInvestigation.id.label("review_id"),
                FraudInvestigation.state.label("review_state"),
                FraudInvestigation.reviewed_by_admin_id.label("reviewed_by_admin_id"),
                FraudInvestigation.updated_at.label("review_updated_at"),
            )
            .outerjoin(
                FraudInvestigation,
                (FraudInvestigation.signal_type == filtered.c.signal_type)
                & (FraudInvestigation.signal_reference == filtered.c.evidence_id),
            )
            .order_by(
                filtered.c.signal_time.desc(),
                filtered.c.signal_type.asc(),
                filtered.c.evidence_id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await session.execute(statement)).all()
        items = [self._signal_data(row) for row in rows]
        device_history, ip_history = await self._list_device_and_ip_history(page_size=page_size)

        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
            "summary": {
                "total_alerts": total,
                "suspicious_transactions": int(aggregate.transactions or 0),
                "accounts_with_failed_logins": int(aggregate.accounts_with_failed_logins or 0),
                "account_security_signals": int(aggregate.account_security_signals or 0),
                "high_risk_accounts": int(aggregate.high_risk_accounts or 0),
                "risk_flags": risk_flags,
            },
            "device_history": device_history,
            "ip_history": ip_history,
        }

    async def review_fraud_signal(
        self,
        *,
        action: str,
        signal_id: str,
        reviewer_admin_id: UUID,
        reason: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        normalized_action = action.strip().lower()
        if normalized_action not in {"review", "dismiss", "escalate", "resolve"}:
            raise ValidationException("Unsupported fraud investigation action.")
        signal_type, evidence_id = self._parse_signal_id(signal_id)
        session = self._session()
        existing_transaction = session.in_transaction()
        transaction = session.begin_nested() if existing_transaction else session.begin()
        async with transaction:
            snapshot = await self._current_evidence(session, signal_type, evidence_id)
            if snapshot is None:
                raise ValidationException("The selected fraud signal no longer has matching persisted evidence.")

            investigation = await session.scalar(
                select(FraudInvestigation)
                .where(
                    FraudInvestigation.signal_type == signal_type,
                    FraudInvestigation.signal_reference == evidence_id,
                )
                .with_for_update()
            )
            if investigation is None:
                created_at = datetime.now(timezone.utc)
                investigation = FraudInvestigation(
                    signal_type=signal_type,
                    signal_reference=evidence_id,
                    state="open",
                    evidence_snapshot=snapshot,
                    reviewed_by_admin_id=reviewer_admin_id,
                    created_at=created_at,
                    updated_at=created_at,
                )
                try:
                    async with session.begin_nested():
                        session.add(investigation)
                        await session.flush()
                except IntegrityError:
                    investigation = await session.scalar(
                        select(FraudInvestigation)
                        .where(
                            FraudInvestigation.signal_type == signal_type,
                            FraudInvestigation.signal_reference == evidence_id,
                        )
                        .with_for_update()
                    )
                    if investigation is None:
                        raise

            previous_state = investigation.state
            next_state = _TRANSITIONS.get((previous_state, normalized_action))
            if next_state is None:
                raise ValidationException(
                    f"Action '{normalized_action}' is not allowed from investigation state '{previous_state}'."
                )

            now = datetime.now(timezone.utc)
            investigation.state = next_state
            investigation.reviewed_by_admin_id = reviewer_admin_id
            investigation.updated_at = now
            session.add(
                FraudInvestigationEvent(
                    investigation_id=investigation.id,
                    admin_id=reviewer_admin_id,
                    action=normalized_action,
                    from_state=previous_state,
                    to_state=next_state,
                    note=reason.strip() if reason and reason.strip() else None,
                    created_at=now,
                )
            )
            session.add(
                AuditLog(
                    actor_type="admin",
                    actor_id=str(reviewer_admin_id),
                    action="fraud_investigation_state_changed",
                    category="fraud_investigation",
                    description="Administrator updated a fraud signal investigation.",
                    resource_type="fraud_investigation",
                    resource_id=str(investigation.id),
                    old_value=json.dumps({"state": previous_state}),
                    new_value=json.dumps({"state": next_state, "action": normalized_action}),
                    change_summary=f"Fraud investigation state changed from {previous_state} to {next_state}.",
                    created_at=now,
                )
            )
            await session.flush()
            result = self._investigation_data(
                investigation,
                last_action=normalized_action,
                last_event_at=now,
            )

        if existing_transaction:
            await session.commit()
        return {"signal_id": signal_id, "investigation": result}

    def _filtered_signal_query(
        self,
        *,
        search: str | None,
        status: str | None,
        risk_level: str | None,
        event_type: str | None,
    ) -> Select[Any]:
        tx_score = self._transaction_score_expression()
        tx_level = self._risk_level_expression(tx_score)
        login_score = self._login_score_expression()
        login_level = self._risk_level_expression(login_score)

        tx_conditions = [
            or_(
                func.lower(Transaction.status).in_(("failed", "reversed", "pending")),
                Transaction.amount >= _HIGH_VALUE,
            )
        ]
        login_conditions = [
            or_(
                User.failed_login_attempts > 0,
                User.account_locked_until.is_not(None),
                User.is_blocked.is_(True),
                User.is_suspended.is_(True),
            )
        ]
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            tx_conditions.append(
                or_(
                    Transaction.reference.ilike(pattern),
                    User.email.ilike(pattern),
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                )
            )
            login_conditions.append(
                or_(
                    User.email.ilike(pattern),
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                )
            )
        if status and status.strip():
            pattern = f"%{status.strip()}%"
            tx_conditions.append(Transaction.status.ilike(pattern))
            login_conditions.append(User.status.ilike(pattern))
        if event_type and event_type.strip():
            pattern = f"%{event_type.strip()}%"
            tx_conditions.append(Transaction.category.ilike(pattern))
            login_conditions.append(literal("authentication").ilike(pattern))
        if risk_level:
            normalized_level = risk_level.strip().upper()
            tx_conditions.append(tx_level == normalized_level)
            login_conditions.append(login_level == normalized_level)

        transaction_signals = (
            select(
                literal("transaction").label("signal_type"),
                Transaction.id.label("evidence_id"),
                Transaction.user_id.label("user_id"),
                Transaction.reference.label("txn_id"),
                cast(Transaction.amount, String).label("amount"),
                tx_score.label("risk_score"),
                tx_level.label("risk_level"),
                Transaction.transaction_type.label("type"),
                Transaction.category.label("event_type"),
                Transaction.provider_name.label("provider"),
                Transaction.created_at.label("signal_time"),
                Transaction.status.label("status"),
                literal(None, type_=Boolean()).label("is_blocked"),
                literal(None, type_=Boolean()).label("is_suspended"),
                literal(None, type_=Integer()).label("failed_attempts"),
                literal(None, type_=DateTime(timezone=True)).label("locked_until"),
                User.first_name.label("first_name"),
                User.last_name.label("last_name"),
                User.email.label("email"),
            )
            .select_from(Transaction)
            .outerjoin(User, User.id == Transaction.user_id)
            .where(*tx_conditions)
        )
        login_signals = (
            select(
                literal("account_security").label("signal_type"),
                User.id.label("evidence_id"),
                User.id.label("user_id"),
                literal(None, type_=String).label("txn_id"),
                literal(None, type_=String).label("amount"),
                login_score.label("risk_score"),
                login_level.label("risk_level"),
                literal("authentication").label("type"),
                literal("authentication").label("event_type"),
                literal(None, type_=String).label("provider"),
                User.updated_at.label("signal_time"),
                User.status.label("status"),
                User.is_blocked.label("is_blocked"),
                User.is_suspended.label("is_suspended"),
                User.failed_login_attempts.label("failed_attempts"),
                User.account_locked_until.label("locked_until"),
                User.first_name.label("first_name"),
                User.last_name.label("last_name"),
                User.email.label("email"),
            )
            .where(*login_conditions)
        )
        return union_all(transaction_signals, login_signals)

    @staticmethod
    def _transaction_score_expression():
        high_amount = Transaction.amount >= _HIGH_VALUE
        normalized_status = func.lower(Transaction.status)
        return case(
            (
                normalized_status.in_(("failed", "reversed")),
                case((high_amount, 85), else_=72),
            ),
            (
                normalized_status == "pending",
                case((high_amount, 60), else_=48),
            ),
            else_=case((high_amount, 36), else_=24),
        )

    @staticmethod
    def _login_score_expression():
        score = 35 + (User.failed_login_attempts * 15) + case(
            (User.account_locked_until.is_not(None), 25),
            else_=0,
        )
        return case((score > 100, 100), else_=score)

    @staticmethod
    def _risk_level_expression(score):
        return case(
            (score >= 80, "CRITICAL"),
            (score >= 65, "HIGH"),
            (score >= 40, "MEDIUM"),
            else_="LOW",
        )

    async def _list_device_and_ip_history(
        self,
        *,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        session = self._session()
        device_rows = (
            await session.execute(
                select(
                    Device.id,
                    Device.user_id,
                    Device.device_name,
                    Device.device_type,
                    Device.is_active,
                    Device.is_trusted,
                    Device.is_revoked,
                    Device.ip_address,
                    Device.last_seen_at,
                    Device.first_seen_at,
                    User.first_name,
                    User.last_name,
                    User.email,
                )
                .join(User, User.id == Device.user_id)
                .order_by(func.coalesce(Device.last_seen_at, Device.first_seen_at).desc(), Device.id.desc())
                .limit(page_size)
            )
        ).all()
        ip_rows = (
            await session.execute(
                select(
                    Device.id,
                    Device.user_id,
                    Device.ip_address,
                    Device.last_seen_at,
                    Device.first_seen_at,
                    User.first_name,
                    User.last_name,
                    User.email,
                )
                .join(User, User.id == Device.user_id)
                .where(Device.ip_address.is_not(None))
                .order_by(func.coalesce(Device.last_seen_at, Device.first_seen_at).desc(), Device.id.desc())
                .limit(page_size)
            )
        ).all()

        device_history = []
        for row in device_rows:
            (
                device_id,
                user_id,
                device_name,
                device_type,
                is_active,
                is_trusted,
                is_revoked,
                ip_address,
                last_seen_at,
                first_seen_at,
                first_name,
                last_name,
                email,
            ) = row
            timestamp = self._iso(last_seen_at or first_seen_at)
            if is_revoked:
                status = "Revoked"
            elif is_active:
                status = "Active"
            else:
                status = "Inactive"
            device_history.append(
                {
                    "id": str(device_id),
                    "user_id": str(user_id) if user_id else None,
                    "user": self._user_label(first_name, last_name, email),
                    "device": device_name or device_type or "Unknown device",
                    "status": status + (" (trusted)" if is_trusted else ""),
                    "time": timestamp,
                    "ip_address": ip_address,
                }
            )
        ip_history = []
        for row in ip_rows:
            device_id, user_id, ip_address, last_seen_at, first_seen_at, first_name, last_name, email = row
            ip_history.append(
                {
                    "id": str(device_id),
                    "user_id": str(user_id) if user_id else None,
                    "user": self._user_label(first_name, last_name, email),
                    "ip": ip_address,
                    "time": self._iso(last_seen_at or first_seen_at),
                }
            )
        return device_history, ip_history

    async def _current_evidence(
        self,
        session,
        signal_type: str,
        evidence_id: UUID,
    ) -> dict[str, Any] | None:
        if signal_type == "transaction":
            row = (
                await session.execute(
                    select(
                        Transaction.id,
                        Transaction.reference,
                        Transaction.status,
                        Transaction.amount,
                        Transaction.category,
                        Transaction.created_at,
                    ).where(
                        Transaction.id == evidence_id,
                        or_(
                            func.lower(Transaction.status).in_(("failed", "reversed", "pending")),
                            Transaction.amount >= _HIGH_VALUE,
                        ),
                    ).with_for_update()
                )
            ).one_or_none()
            if row is None:
                return None
            return {
                "signal_type": signal_type,
                "evidence_id": str(row.id),
                "reference": row.reference,
                "status": row.status,
                "amount": str(row.amount),
                "event_type": row.category,
                "observed_at": self._iso(row.created_at),
            }

        row = (
            await session.execute(
                select(
                    User.id,
                    User.failed_login_attempts,
                    User.account_locked_until,
                    User.is_blocked,
                    User.is_suspended,
                    User.updated_at,
                ).where(
                    User.id == evidence_id,
                    or_(
                        User.failed_login_attempts > 0,
                        User.account_locked_until.is_not(None),
                        User.is_blocked.is_(True),
                        User.is_suspended.is_(True),
                    ),
                ).with_for_update()
            )
        ).one_or_none()
        if row is None:
            return None
        return {
            "signal_type": signal_type,
            "evidence_id": str(row.id),
            "failed_attempts": int(row.failed_login_attempts or 0),
            "locked_until": self._iso(row.account_locked_until),
            "is_blocked": bool(row.is_blocked),
            "is_suspended": bool(row.is_suspended),
            "observed_at": self._iso(row.updated_at),
        }

    def _signal_data(self, row) -> dict[str, Any]:
        (
            signal_type,
            evidence_id,
            user_id,
            txn_id,
            amount,
            risk_score,
            risk_level,
            transaction_type,
            event_type,
            provider,
            signal_time,
            status,
            is_blocked,
            is_suspended,
            failed_attempts,
            locked_until,
            first_name,
            last_name,
            email,
            review_id,
            review_state,
            reviewed_by_admin_id,
            review_updated_at,
        ) = row
        reasons = []
        if signal_type == "transaction":
            normalized_status = str(status or "").lower()
            if normalized_status in {"failed", "reversed", "pending"}:
                reasons.append(f"Transaction status is {normalized_status}.")
            if amount is not None and Decimal(str(amount)) >= _HIGH_VALUE:
                reasons.append("Transaction amount is at least 50,000; no currency normalization is applied.")
        else:
            if failed_attempts:
                reasons.append(f"Currently recorded failed-login attempts: {int(failed_attempts)}.")
            if locked_until is not None:
                reasons.append("An account lock expiration is recorded.")
            if is_blocked:
                reasons.append("The account is marked blocked.")
            if is_suspended:
                reasons.append("The account is marked suspended.")

        return {
            "signal_id": f"{signal_type}:{evidence_id}",
            "signal_type": signal_type,
            "evidence_id": str(evidence_id),
            "user_id": str(user_id),
            "user": self._user_label(first_name, last_name, email),
            "risk_score": int(risk_score),
            "risk_level": risk_level,
            "classification": "suspicious_signal",
            "risk_reasons": reasons,
            "date_label": "transaction_created" if signal_type == "transaction" else "account_record_updated",
            "txn_id": txn_id,
            "amount": amount,
            "type": transaction_type,
            "event_type": event_type,
            "provider": provider,
            "date": self._iso(signal_time),
            "status": status,
            "failed_attempts": int(failed_attempts) if failed_attempts is not None else None,
            "locked_until": self._iso(locked_until) if locked_until else None,
            "investigation": (
                {
                    "id": str(review_id),
                    "state": review_state,
                    "reviewed_by_admin_id": str(reviewed_by_admin_id),
                    "updated_at": self._iso(review_updated_at),
                }
                if review_id
                else None
            ),
        }

    def _investigation_data(
        self,
        investigation: FraudInvestigation,
        *,
        last_action: str,
        last_event_at: datetime,
    ) -> dict[str, Any]:
        return {
            "id": str(investigation.id),
            "signal_type": investigation.signal_type,
            "signal_id": f"{investigation.signal_type}:{investigation.signal_reference}",
            "state": investigation.state,
            "reviewed_by_admin_id": str(investigation.reviewed_by_admin_id),
            "evidence_snapshot": investigation.evidence_snapshot,
            "last_action": last_action,
            "updated_at": self._iso(last_event_at),
        }

    @staticmethod
    def _parse_signal_id(signal_id: str) -> tuple[str, UUID]:
        try:
            signal_type, raw_id = signal_id.split(":", 1)
            if signal_type not in {"transaction", "account_security"}:
                raise ValueError
            return signal_type, UUID(raw_id)
        except (ValueError, AttributeError) as exc:
            raise ValidationException("signal_id must identify a transaction or account-security signal.") from exc

    @staticmethod
    def _user_label(first_name: str | None, last_name: str | None, email: str | None) -> str:
        parts = [part for part in (first_name, last_name) if part]
        if parts:
            return " ".join(parts)
        if email and "@" in email:
            local, domain = email.split("@", 1)
            return f"{local[:1]}***@{domain}"
        return "Unknown user"

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if isinstance(value, datetime) else None

    def _session(self):
        repository = self.transaction_repository or self.user_repository
        session = getattr(repository, "session", None)
        if session is None:
            raise RuntimeError("FraudService requires a request-scoped database session.")
        return session
