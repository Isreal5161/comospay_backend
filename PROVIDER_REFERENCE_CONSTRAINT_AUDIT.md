# CosmozPay Provider Reference Constraint Pre-Implementation Audit

**Date:** 2026-08-16  
**Mode:** READ-ONLY / Evidence-first assessment  
**Objective:** Determine the correct uniqueness constraint for `Transaction.provider_reference`

---

## 1. Executive Summary

### Question
Should a database uniqueness constraint be added to `Transaction.provider_reference`?  
If yes, what is the correct constraint?

### Finding
**APPROVED WITH MODIFICATION**

**Correct constraint:** `UNIQUE(provider_name, provider_reference)`

**NOT:** `UNIQUE(provider_reference)` (global)

**Reason:** Different payment providers generate reference IDs in independent namespaces. A global uniqueness constraint would incorrectly reject valid transactions from different providers with overlapping IDs.

---

## 2. Data Model Analysis

### Transaction Model
**File:** [app/models/transaction.py](app/models/transaction.py)

```python
provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
```

**Current constraints:**
```python
__table_args__ = (
    UniqueConstraint("reference", name="uq_transactions_reference"),
    Index("ix_transactions_provider_ref", "provider_name", "provider_reference"),
)
```

**Observations:**
- `provider_reference` is **nullable**
- `provider_name` is **nullable**
- An index already exists on `(provider_name, provider_reference)` for query performance
- **NO unique constraint currently exists on provider_reference**

### VirtualAccount Model
**File:** [app/models/virtual_account.py](app/models/virtual_account.py)

```python
provider: Mapped[str] = mapped_column(String(100), nullable=False, ...)
provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, ...)
```

**Current constraints:**
```python
UniqueConstraint("provider", "account_number", name="uq_virtual_accounts_provider_account_number"),
UniqueConstraint("provider_reference", name="uq_virtual_accounts_provider_reference"),
UniqueConstraint("provider_account_id", name="uq_virtual_accounts_provider_account_id"),
```

**Observations:**
- `provider_reference` has a **GLOBAL unique constraint**
- This is intentional for virtual accounts because each virtual account is a unique provider-issued bank account that belongs to exactly one wallet
- This is a different use case than Transaction

### BankAccount Model
**File:** [app/models/bank_account.py](app/models/bank_account.py)

```python
provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
```

**Current constraints:**
```python
Index("ix_bank_accounts_provider_ref", "provider_name", "provider_reference"),
```

**Observations:**
- Index on `(provider_name, provider_reference)` but **NO unique constraint**
- This suggests the design intent is provider-scoped, not global

---

## 3. Write Paths Analysis

### Path 1: Wallet Funding (`app/services/wallet/funding.py`)

**Function:** `initialize_wallet_funding()`

**Code:**
```python
await self._ensure_provider_reference_is_unique(
    provider_name=provider_name, 
    provider_reference=provider_reference
)
transaction = Transaction(
    ...
    provider_name=provider_name,
    provider_reference=provider_reference,
)
```

**Uniqueness Check:**
```python
async def _ensure_provider_reference_is_unique(self, *, provider_name: str, provider_reference: str | None) -> None:
    if not provider_reference:
        return
    result = await session.execute(
        session.query(Transaction).filter(Transaction.provider_reference == provider_reference)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise WalletException("Duplicate provider reference detected.")
```

**Critical Issue:** The check is **GLOBAL**, not provider-scoped:
- Checks: `Transaction.provider_reference == provider_reference`
- **Does NOT filter by provider_name**
- This means Flutterwave ref "12345" would block Aidapay ref "12345"

### Path 2: Virtual Account Funding (`app/services/wallet/funding.py`)

**Function:** `process_virtual_account_funding()`

**Code:**
```python
payload = await self._dispatch_provider_call("create_virtual_account", ...)
transaction = Transaction(
    ...
    provider_reference=payload.get("provider_reference"),
)
```

**Uniqueness Check:** **NONE** — No call to `_ensure_provider_reference_is_unique()`

### Path 3: Payment Initialization (`app/services/payment/payment.py`)

**Function:** `initialize_payment()`

**Code:**
```python
provider_response = await self.provider_service.execute_payment(...)
transaction.provider_reference = provider_response.get("provider_reference")
```

**Uniqueness Check:** **NONE**

### Path 4: Payment Verification (`app/services/payment/verification.py`)

**Function:** `verify_payment()`

**Code:**
```python
response = await self._dispatch_provider(...)
transaction.provider_reference = response.get("provider_reference") or transaction.provider_reference
await self.transaction_repository.update_transaction(transaction, ...)
```

**Uniqueness Check:** **NONE** — Updates existing transaction

### Path 5: Webhook Processing (`app/services/payment/webhook.py`)

**Function:** `process_payment_webhook()`

**Code:**
```python
transaction = await self.transaction_repository.get_by_reference_for_update(reference)
transaction.provider_reference = self._extract_provider_reference(payload)
transaction = await self.transaction_repository.update_transaction(transaction, ...)
```

**Uniqueness Check:** **NONE** — Updates existing transaction

### Path 6: Reconciliation (Multiple services)

**Files:** `app/services/*/reconciliation.py`

**Code pattern:**
```python
transaction.provider_reference = provider_response.get("provider_reference") or transaction.provider_reference
```

**Uniqueness Check:** **NONE**

### Path 7: Retry/Repeated Provider Calls

**Files:** Various services

**Pattern:** When a provider call is retried or a transaction is re-verified, `provider_reference` can be updated multiple times. Each time, it comes from the provider response, not generated locally.

**Uniqueness Check:** **NONE**

### Summary Table

| Path | Provider | provider_reference source | Can be NULL? | Uniqueness check |
|------|----------|---------------------------|--------------|------------------|
| Wallet funding init | Yes (arg) | Argument or NULL | Yes | **GLOBAL check only** |
| Virtual account init | Yes (from provider) | Provider response | Yes | None |
| Payment init | Yes (from provider) | Provider response | Yes | None |
| Payment verification | Updates existing | Provider response | Yes | None |
| Webhook | Updates existing | Webhook payload | Yes | None |
| Reconciliation | Updates existing | Provider response | Yes | None |
| Retry/re-call | Existing | Provider response | Yes | None |

---

## 4. Read/Lookup Paths Analysis

### Lookup by provider_reference

**File:** `app/services/wallet/funding.py`

```python
async def _get_transaction(self, *, provider_reference: str | None = None) -> Transaction | None:
    if provider_reference is not None:
        result = await session.execute(
            session.query(Transaction).filter(Transaction.provider_reference == provider_reference)
        )
        return result.scalar_one_or_none()
    return None
```

**Issue:** This query is **GLOBAL** and could return the wrong transaction if multiple transactions share the same provider_reference from different providers.

### Webhook Matching

**File:** `app/services/payment/webhook.py`

```python
reference = self._extract_reference(payload)  # Internal reference, not provider_reference
transaction = await self.transaction_repository.get_by_reference_for_update(reference)
```

**Safe:** Uses internal `reference`, not `provider_reference`

### Provider Callback Matching

In retry and reconciliation scenarios, if a provider callback arrives with a provider_reference, the system would need to find the associated transaction. With a global provider_reference, this could return the wrong transaction if multiple providers generated the same ID.

---

## 5. Provider Semantics Analysis

### Multi-Provider Architecture

The application uses a provider failover architecture with multiple independent providers:

**Evidence from code:**
- `app/services/provider_service.py` - provider orchestration
- `app/services/provider/selector.py` - provider selection
- `app/services/provider/failover.py` - failover logic

**Providers identified in codebase:**
- Flutterwave
- Aidapay
- VTUGate
- ClubConnect
- VTU.ng
- Paystack
- Monnify
- Korapay

### Provider ID Namespace Independence

**Assumption:** Do different providers generate independent reference ID namespaces?

**Evidence:**
1. **Index design:** BankAccount and Transaction both have `Index("ix_..._provider_ref", "provider_name", "provider_reference")` — this strongly suggests provider-scoped lookup is intended
2. **VirtualAccount design:** Uses both `UNIQUE("provider", "account_number")` AND `UNIQUE("provider_reference")` — the latter is global, but virtual accounts are provider-issued bank accounts (different use case)
3. **Provider payload extraction:** Each provider integration extracts its own reference format
   - Flutterwave: `payload.get("provider_reference")`
   - Aidapay: `payload.get("ref")`
   - VTU.ng: `payload.get("transaction_hash")`

**Conclusion:** Different providers use independent ID namespaces. A global uniqueness constraint would incorrectly collide across providers.

---

## 6. Existing Data Analysis

### Database Status
**Status:** Development/test database structure verified  
**Note:** SQLite development database does not have populated data for analysis

**Alternative evidence:**
- Test suite creates transactions with provider_reference values (see tests)
- No test prevents duplicate provider_reference across different providers
- No test asserts global uniqueness

### NULL Handling
**SQL Uniqueness semantics:** UNIQUE constraints treat multiple NULL values as distinct (standard SQL)

**Implication:** With `UNIQUE(provider_name, provider_reference)`:
- (NULL, "ref-123") — allowed
- (NULL, "ref-123") — would be allowed (NULLs are distinct)
- ("flutterwave", "ref-123") — allowed
- ("aidapay", "ref-123") — allowed (different provider)

This is correct behavior.

---

## 7. NULL Analysis

### Current NULL Handling

**Transaction model:**
```python
provider_name: Mapped[str | None]  # nullable
provider_reference: Mapped[str | None]  # nullable
```

**Valid scenarios with NULL:**
1. A transaction created without provider information initially (status: pending)
2. A transaction that is internal/non-provider (e.g., wallet transfer)
3. A transaction awaiting provider assignment during retry/failover

**With UNIQUE(provider_name, provider_reference):**
- Multiple transactions with (NULL, NULL) would be allowed ✓
- Multiple transactions with ("flutterwave", NULL) would be allowed ✓
- Only one transaction per (provider_name, provider_reference) non-NULL pair would be allowed ✓

This is correct behavior.

---

## 8. Migration Safety Analysis

### Current State
- No unique constraint on provider_reference in Transaction
- The application-level check in wallet funding is incomplete
- Multiple code paths can set provider_reference without checking

### Potential Issues
1. **Existing duplicates:** If the database already has duplicate provider_reference values, adding a constraint would fail
2. **Data cleanup:** Would need to inspect existing data before migration
3. **Rollback:** If migration fails, transactions could be left in inconsistent state

### Migration Strategy (Not implemented, planning only)
1. Check for existing duplicate (provider_name, provider_reference) pairs
2. If duplicates exist, implement a cleanup strategy:
   - Keep the oldest transaction, mark others as "merged"
   - Or keep the one with the most complete metadata
3. Add NOT NULL constraint to provider_reference if business requires it (currently it's nullable)
4. Create migration to add the unique constraint
5. Validate no duplicates after migration

---

## 9. Security Analysis

### What Does This Constraint Actually Prevent?

#### Scenario 1: Duplicate Wallet Credit (Same provider)
- User initiates wallet funding with provider_reference "FLW-12345"
- Payment is verified, wallet is credited
- Somehow another transaction is created with the same provider_reference "FLW-12345"
- The constraint would prevent this ✓

**Current protection:** Row locking + `wallet_credit_applied` metadata flag + status checks

**Constraint benefit:** Adds database-level enforcement

#### Scenario 2: Replay Attack Across Providers
- Flutterwave sends callback with reference "12345"
- Attacker sends fake Aidapay callback with same reference "12345"
- Without provider-scoped constraint, both would be accepted
- With `UNIQUE(provider_name, provider_reference)`, the second would be rejected ✓

#### Scenario 3: Reconciliation Ambiguity
- Flutterwave provider_reference "TX-001" exists
- Aidapay provider_reference "TX-001" is created
- A provider callback arrives with "TX-001"
- Which transaction should it update?
- With global uniqueness: collision (bad)
- With provider-scoped uniqueness: clear (good)

### What This Constraint Does NOT Prevent

#### Scenario 1: Double-Credit via Different Providers
- Transaction funded via Flutterwave: provider_reference "FLW-12345"
- Same transaction credited via Aidapay somehow (code bug)
- Constraint doesn't help because they're different provider_reference values

**Current protection:** Business logic validation, status checks, metadata flags

#### Scenario 2: Webhook Signature Bypass
- Attacker forges webhook signature
- Constraint doesn't help; signature validation must catch this

**Current protection:** HMAC signature validation already present

---

## 10. Financial Integrity Impact

### Current Risks
1. **Inconsistent deduplication:** Wallet funding checks for duplicates, but payment verification and webhooks don't
2. **Provider reference ambiguity:** Without constraint, multiple transactions could share same provider_reference
3. **Reconciliation confusion:** If a provider callback arrives, which transaction should it credit?

### After Adding UNIQUE(provider_name, provider_reference)
1. **Database-enforced deduplication** across all write paths
2. **Clear provider_reference semantics:** Each (provider, reference) pair belongs to exactly one transaction
3. **Safe provider callback matching:** A provider callback with (provider_name, provider_reference) unambiguously identifies a transaction

### No Impact On
- Double-credit prevention (already protected by row locks + status checks)
- Wallet balance correctness (already protected by constraints and ledger)
- Transaction idempotency (already protected by status flags)

---

## 11. Architecture Impact

### Existing Architecture
```
Controller → Service → Repository → Database
    ↓
Provider-scoped business logic
Stateless provider selection
Row-level locking
Status-based idempotency
```

### Impact of Constraint

**Positive:**
- Adds database-level integrity enforcement
- Complements existing application-level checks
- No business logic changes required
- Supports multi-provider architecture correctly

**Neutral:**
- No change to controller, service, or repository interfaces
- No change to failover or retry logic
- No change to provider selection
- No architectural redesign

**No Negative Impact** identified

---

## 12. Existing Mitigations Assessment

### Current Protections
1. **Row-level locking** on wallet and transaction updates — prevents concurrent modification
2. **Status-based idempotency** — `wallet_credit_applied` metadata flag prevents double-credit
3. **Unique internal reference** — `transactions.reference` is globally unique
4. **Webhook duplicate filtering** — event-ID tracking prevents duplicate webhook processing
5. **Application-level check** in wallet funding — but incomplete (global, not provider-scoped)

### Why Constraint is Still Needed
- Current application-level check is incomplete (only in wallet funding)
- Verification and webhook paths don't check provider_reference uniqueness
- Row locking prevents concurrent modification but doesn't prevent sequential duplicate creation
- Metadata flag prevents double-credit but doesn't prevent duplicate transaction creation

---

## 13. False Positives Check

### Is This Constraint Over-Engineering?
**No.** Evidence:
- Different models use provider-scoped patterns (BankAccount index, implicit VirtualAccount design)
- Multiple write paths don't check uniqueness (verification, webhook, reconciliation)
- Provider architecture uses independent namespaces
- Existing application-level check acknowledges the need but is incomplete

### Is This a False Positive Finding from the Audit?
**No.** Evidence:
- The inconsistency is real (check only in wallet funding)
- The design is unclear (app-level check is global, not provider-scoped)
- The risk is real (multiple paths can set provider_reference)

---

## 14. Final Decision

### Decision: **APPROVED WITH MODIFICATION**

### Recommended Constraint

**CHANGE FROM:** (implied global uniqueness)  
**CHANGE TO:**
```python
UniqueConstraint("provider_name", "provider_reference", name="uq_transactions_provider_ref")
```

### NOT This:
```python
UniqueConstraint("provider_reference", name="uq_transactions_provider_ref")  # WRONG - global
```

### Rationale
1. Different payment providers use independent reference ID namespaces
2. The existing index on `(provider_name, provider_reference)` indicates this is the intended design
3. BankAccount model uses provider-scoped pattern
4. Application-level check is currently global, but should be provider-scoped
5. Multi-provider failover requires provider-scoped uniqueness
6. Supports safe provider callback matching

---

## 15. Implementation Plan (Not Implemented — Planning Only)

### Phase 1: Pre-Migration Data Validation (Non-destructive)

**Goal:** Identify any existing duplicates or conflicts

**Steps:**
1. Query existing transaction data for duplicate (provider_name, provider_reference) pairs
2. Identify transactions with NULL provider_name or NULL provider_reference
3. Document findings

**Effort:** 30 minutes  
**Risk:** Read-only, zero risk

### Phase 2: Create Alembic Migration

**Goal:** Add the unique constraint to the database

**Migration file:** `alembic/versions/2026_08_16_add_provider_reference_uniqueness.py`

**Steps:**
```python
# Migration snippet
def upgrade():
    # Add unique constraint on (provider_name, provider_reference)
    op.create_unique_constraint(
        'uq_transactions_provider_ref',
        'transactions',
        ['provider_name', 'provider_reference']
    )

def downgrade():
    op.drop_constraint('uq_transactions_provider_ref', 'transactions')
```

**Effort:** 30 minutes  
**Risk:** Low if data is clean; medium if duplicates exist

### Phase 3: Update SQLAlchemy Model

**File:** [app/models/transaction.py](app/models/transaction.py)

**Change:**
```python
__table_args__ = (
    UniqueConstraint("reference", name="uq_transactions_reference"),
    UniqueConstraint("provider_name", "provider_reference", name="uq_transactions_provider_ref"),
    Index("ix_transactions_user_created", "user_id", "created_at"),
    # ... other indexes ...
)
```

**Effort:** 10 minutes  
**Risk:** Very low (model change only)

### Phase 4: Simplify Application-Level Check

**File:** [app/services/wallet/funding.py](app/services/wallet/funding.py)

**Current:**
```python
async def _ensure_provider_reference_is_unique(self, *, provider_name: str, provider_reference: str | None) -> None:
    if not provider_reference:
        return
    result = await session.execute(
        session.query(Transaction).filter(Transaction.provider_reference == provider_reference)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise WalletException("Duplicate provider reference detected.")
```

**Recommended change:**
```python
async def _ensure_provider_reference_is_unique(self, *, provider_name: str, provider_reference: str | None) -> None:
    # After database constraint is in place, this becomes a secondary validation
    # Can be optimized or kept as defensive programming
    if not provider_reference or not provider_name:
        return
    result = await session.execute(
        session.query(Transaction).filter(
            (Transaction.provider_name == provider_name) &
            (Transaction.provider_reference == provider_reference)
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise WalletException(f"Duplicate provider reference for {provider_name}: {provider_reference}")
```

**Effort:** 15 minutes  
**Risk:** Low (defensive improvement)

**Note:** This change is OPTIONAL. The database constraint is the primary protection. The application-level check can remain as secondary defense.

### Phase 5: Add Tests

**Tests to add:**

1. **Constraint validation test:**
```python
async def test_provider_reference_uniqueness_per_provider():
    """Test that same provider_reference can exist for different providers."""
    # Create transaction with Flutterwave ref "12345"
    # Create transaction with Aidapay ref "12345"
    # Both should succeed
    # Attempting to create another with Flutterwave ref "12345" should fail
```

2. **Webhook idempotency test:**
```python
async def test_webhook_with_duplicate_provider_reference_same_provider():
    """Test that duplicate provider_reference from same provider is rejected."""
    # Create transaction with Flutterwave ref "12345"
    # Process webhook with Flutterwave ref "12345" twice
    # Second should be idempotent, not create duplicate
```

3. **Migration compatibility test:**
```python
async def test_payment_verification_updates_existing_transaction():
    """Verify that payment verification updates provider_reference on existing transaction."""
    # This ensures updating an existing transaction's provider_reference still works
```

**Effort:** 1-2 hours  
**Risk:** Very low (new tests, not modifying existing)

### Phase 6: Regression Testing

**Execute existing test suite:**
```bash
PYTHONPATH=. pytest -q tests/
```

**Expected result:** All 167 tests pass  
**Effort:** 30 minutes  
**Risk:** Very low

### Phase 7: Deployment

**Steps:**
1. Run Alembic migration on staging database
2. Verify no errors
3. Run test suite on staging
4. Deploy migration to production
5. Monitor transaction creation for constraint violations

**Effort:** 1 hour  
**Risk:** Low if data is clean

---

## 16. Required Tests

### Unit Tests

1. ✓ Database constraint prevents duplicate (provider_name, provider_reference)
2. ✓ Database constraint allows same provider_reference for different providers
3. ✓ Database constraint allows multiple NULL values
4. ✓ Wallet funding still works after constraint added
5. ✓ Payment verification still works after constraint added

### Integration Tests

1. ✓ Multi-provider transaction creation (Flutterwave + Aidapay)
2. ✓ Webhook processing with provider_reference
3. ✓ Reconciliation with provider_reference
4. ✓ Retry scenarios maintain provider_reference

### Regression Tests

1. ✓ Full test suite (167 tests) passes
2. ✓ Security tests (SEC-001 through SEC-005) pass
3. ✓ Scalability tests (SCAL-001 through SCAL-005) pass
4. ✓ Payment flow end-to-end tests pass
5. ✓ Wallet funding end-to-end tests pass

---

## 17. Deployment Considerations

### Zero-Downtime Deployment
1. Add constraint with `NOT ENFORCED` initially (if database supports)
2. Run validation script to find violations
3. Fix violations
4. Enable constraint enforcement
5. Deploy application code

### Monitoring Post-Deployment
1. Monitor for constraint violation errors in production logs
2. Alert on: `"duplicate key value violates unique constraint"`
3. Verify that provider_reference lookups still work correctly
4. Performance monitoring on transaction creation

### Rollback Strategy
If constraint causes issues in production:
1. Disable constraint: `ALTER TABLE transactions DROP CONSTRAINT uq_transactions_provider_ref`
2. Revert application code changes
3. Investigate root cause
4. Re-apply after fix

---

## 18. Security & Compliance Impact

### Security Improvement
- ✓ Adds database-level enforcement of provider identity deduplication
- ✓ Prevents replay attacks across different providers
- ✓ Supports safe provider callback matching
- ✓ Reduces reconciliation ambiguity

### Compliance Impact
- ✓ Improves data integrity auditability
- ✓ Supports financial transaction uniqueness requirements
- ✓ No negative compliance impact

---

## 19. Summary

| Aspect | Assessment |
|--------|------------|
| Constraint needed? | YES |
| Correct constraint | `UNIQUE(provider_name, provider_reference)` |
| Global uniqueness needed? | NO — different providers have independent namespaces |
| Current application protection sufficient? | NO — check only in one path, and it's global |
| Database-level enforcement needed? | YES — complements and strengthens existing controls |
| Backward compatibility | YES — constraint only prevents invalid states |
| Existing data impact | Unknown (database not populated in test env) |
| Implementation complexity | LOW |
| Testing complexity | LOW |
| Deployment complexity | MEDIUM (migration + monitoring) |
| Risk level | LOW |

---

## Final Recommendation

**✅ APPROVED WITH MODIFICATION**

**Implement:** `UNIQUE(provider_name, provider_reference)`  
**Do NOT implement:** `UNIQUE(provider_reference)` (global)

**Estimated effort:**
- Planning & testing: 2-3 hours
- Migration creation: 1 hour
- Implementation: 1-2 hours
- Deployment & monitoring: 2-3 hours

**Total: 6-9 hours**

**Next step:** Await explicit user approval before proceeding with implementation.
