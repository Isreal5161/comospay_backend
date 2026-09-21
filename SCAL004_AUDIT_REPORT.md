# SCAL-004 Transaction Retry / Provider Idempotency Audit Report

**Status:** ✅ **NO CONFIRMED VULNERABILITY IN THE CORE WALLET-FUNDING RETRY PATH**

**Audit Phase:** READ-ONLY investigation complete (No application code modified)

**Date:** Post-SCAL-003 provider-selection audit
**Scope:** Transaction identity, retry semantics, provider uncertainty, wallet debit/credit safety, and finalization logic for logical retries
**Constraint:** No code changes, no assumptions, and stop after the report if a genuine vulnerability is confirmed

---

## Executive Summary

This review examined whether a logical transaction can be retried safely when the previous provider outcome is uncertain, whether the same wallet funding operation can be credited or reversed twice, and whether replayed provider callbacks or retry requests can create duplicate financial effects.

The strongest evidence shows that the core wallet-funding path is not vulnerable to the classic double-credit scenario under normal retry and verification logic:

- internal transaction identity is unique at the database level (`transactions.reference` has a unique constraint)
- wallet credit is serialized with row locks (`get_by_reference_for_update` / `get_by_id_for_update`)
- crediting is gated by both status checks and metadata flags (`wallet_credit_applied`)
- provider-facing initialization uses a stable idempotency key based on the canonical transaction reference
- retries re-enter only against the same canonical transaction, not a new business transaction record

However, there is an important caveat: the system is not completely idempotent across every transaction type and every provider callback path. The `provider_reference` column is not backed by a unique database constraint, and some non-funding payment flows do not use the same locking and metadata guards as the wallet-funding workflow. That means duplicate external callbacks or concurrent verification could be a real integrity risk in other payment paths, even if the audited wallet-funding flow itself is comparatively protected.

This report therefore reaches a narrow but evidence-based conclusion:

- For the wallet-funding retry path, there is no confirmed vulnerability from the reviewed code paths.
- For the broader payment/provider ecosystem, there is a residual integrity gap that should be treated as a design risk and a future hardening target, but not as a confirmed exploit in the funding flow reviewed here.

---

## 1. Evidence by Layer

### 1.1 Transaction identity is unique and protected by the database

Primary evidence:
- `app/models/transaction.py` defines `Transaction.reference` as a unique column with `UniqueConstraint("reference", name="uq_transactions_reference")`.
- `app/repositories/transaction_repository.py` exposes `get_by_reference()` and `get_by_reference_for_update()`.
- `WalletFundingService.initialize_wallet_funding()` creates a `Transaction` using a new reference generated from `uuid4()` before provider dispatch.

Implication:
- A retry of the same logical funding request does not create a second canonical transaction record if the caller uses the same internal reference and the database enforces uniqueness.
- The system has a strong internal idempotency boundary at the business record level.

This is the most important positive finding. The app is not creating a free-form duplicate payment transaction for the same logical action without a unique identity check.

### 1.2 Row locking prevents concurrent wallet mutation races

Primary evidence:
- `TransactionRepository.get_by_reference_for_update()` and `get_by_id_for_update()` both use `SELECT ... WITH FOR UPDATE`.
- `WalletFundingService.credit_wallet()` calls `_get_transaction_for_update()` before mutating the wallet.
- `PaymentVerificationService.verify_payment()` also re-fetches the transaction under lock before updating status and crediting.

Implication:
- Two workers that race to finalize the same transaction cannot both mutate the same row at the same time without serializing.
- The wallet credit path uses a lock to make the state transition effectively atomic.

This matters because the central threat model is: one logical transaction is retried after a timeout or network ambiguity, and two workers both try to apply the successful result. The lock prevents the raw double-credit race in the funding flow.

### 1.3 Crediting is guarded by both status and metadata checks

Primary evidence:
- In `app/services/wallet/funding.py`, `credit_wallet()` does:
  - `if metadata.get("wallet_credit_applied") is True: return ...`
  - `if transaction.status.lower() in {"completed", "succeeded", "credited"}: return ...`
  - `if transaction.status.lower() in {"reversed", "failed", "cancelled", "voided"}: raise WalletException(...)`
- After crediting, it updates metadata to include `{"credited": True, "wallet_credit_applied": True}`.

Implication:
- A second pass is explicitly prevented even if the same transaction is retried.
- This is not just a status check; it is an application-level idempotency marker.

This is a significant reason the wallet-funding flow is safe against duplicate wallet credits after a successful provider result is observed twice.

### 1.4 Provider initialization uses deterministic idempotency keys

Primary evidence:
- `app/integrations/payments/flutterwave/utils.py` defines `build_idempotency_key(*values)` as a stable hash-like key derived from its inputs.
- `app/integrations/payments/flutterwave/payments.py` sets `headers["Idempotency-Key"] = build_idempotency_key("payment-init", tx_ref or request_id)`.
- The internal transaction reference (`tx_ref`) is reused across retries when the same logical request is being retried.

Implication:
- A provider re-attempt using the same transaction reference is not treated as a new provider payment by the upstream provider if the provider honors the idempotency key.
- This materially reduces duplicate external execution when a caller retries the same logical payment within the provider contract.

This aligns with the intended payment-provider semantics: repeated requests for the same transaction reference should not produce a second provider charge when the upstream API supports idempotency keys.

### 1.5 HTTP-layer idempotency is separate from transaction-level idempotency

Primary evidence:
- `app/middleware/idempotency_middleware.py` protects duplicate HTTP requests by checking `Idempotency-Key` at the API layer.
- `app/services/webhook/idempotency.py` protects webhook processing by setting a Redis lock for repeated event IDs.

Implication:
- This prevents client or webhook replay at the transport boundary.
- It does not automatically prove a business transaction is safe in all retry scenarios, because it is not a substitute for database uniqueness, row locks, or transaction-level status guards.

This is important context: request replay safety is real, but it is not the same as logical transaction idempotency.

---

## 2. Retry / Uncertain-Result Scenarios

### Scenario A — Same logical transaction retried after timeout

Observed behavior:
- The canonical transaction record stays fixed because `reference` is unique and looked up by reference.
- The retry path re-enters the same transaction row, not a newly created transaction.
- If the provider call is uncertain, the app uses verification or reconciliation based on the same reference.

Assessment:
- In the wallet-funding flow, this is safe because the system re-reads the transaction under lock and will not credit a second time once the metadata flag is set.

### Scenario B — Provider says success but response lost before app records it

Observed behavior:
- The app may later call `verify_wallet_funding(reference=...)` or `reconcile_wallet_funding(reference=...)`.
- Those paths check status and metadata before issuing wallet credit.
- The first successful pass sets `wallet_credit_applied` and marks the transaction as completed.

Assessment:
- This is the main place where a duplicate financial action would occur if locking or guard logic were absent.
- Here, the row lock and metadata guard materially reduce the risk.

### Scenario C — Duplicate callback arrives after a successful webhook

Observed behavior:
- `PaymentWebhookService.process_payment_webhook()` tracks `processed_event_ids` in metadata and ignores duplicates.
- It also updates transaction status and credits the wallet once, guarded by `wallet_credit_applied`.

Assessment:
- This is robust in the reviewed payment webhook path.
- The duplicate-event fencing is more explicit than the generic `provider_reference` uniqueness check.

### Scenario D — Admin retry after a failed or ambiguous transaction

Observed behavior:
- `TransactionAdministrationService.retry_failed_transaction()` routes to `verify_wallet_funding()` for wallet funding transactions.
- `verify_wallet_funding()` exits early when status is already terminal (`completed`, `succeeded`, `credited`).

Assessment:
- A second admin retry is not allowed to create a duplicate credit once the transaction has already succeeded.
- This makes the admin-driven retry path materially safer than a naive “retry blindly” design.

---

## 3. The Actual Gap: Provider Identity Is Not Uniquely Enforced in the Database

This is the main caveat and the most important non-trivial finding.

Primary evidence:
- `app/models/transaction.py` has an index on `(provider_name, provider_reference)` but no unique constraint on `provider_reference`.
- `app/services/wallet/funding.py` contains an app-level check `_ensure_provider_reference_is_unique(...)`, but it is not backed by a database-level unique constraint and is implemented as a query-based validation rather than a schema guarantee.
- The method is not equivalent to `UNIQUE(provider_reference)` because it is not enforced at the database layer.

Why this matters:
- If two request paths create different internal `Transaction.reference` values but reuse the same provider reference, the system can accept both rows.
- That creates an integrity hole in the event that a provider replays or the same provider call is duplicated outside the canonical internal transaction identity.
- It is a real schema-level weakness, even though the wallet funding retry path itself is comparatively protected by row locks and metadata checks.

This gap is not the same as a confirmed exploit, but it is relevant to the user’s question: can the same logical provider result be safely retried when the outcome is uncertain? It can be safely retried in the audited wallet path because the reference is internally unique, but the system does not completely guarantee that the provider reference itself is globally unique across all transaction rows.

---

## 4. Conclusion

### Finding 1 — Core wallet-funding retry safety

For the primary wallet-funding workflow, the system has the main safety properties needed for safe retry after an uncertain provider result:

- unique transaction identity
- database row locking during finalization
- status-based guard rails
- metadata-based idempotency flag
- stable provider idempotency key for provider initialization

Based on the audited code paths, the same logical wallet-funding transaction is not being double-credited under normal retry and verification conditions.

### Finding 2 — Not all business flows are equally protected

The broader system is not uniformly idempotent across all payment flows. The lack of a unique database constraint on `provider_reference` and the absence of equivalent lock-guard patterns in some payment paths mean that a duplicate provider callback or concurrent verification could still create a second logical financial effect in non-funding flows.

This is not a confirmed vulnerability in the wallet-funding branch reviewed most closely, but it is a valid residual risk that needs explicit design acknowledgement.

### Final assessment

**Conclusion:** No confirmed vulnerability was found in the audited wallet-funding retry path. The system is reasonably safe against an internal double-credit race when the same logical transaction is retried after an uncertain provider result. However, the application is not fully provider-idempotent across every path because the database does not enforce uniqueness on `provider_reference`, leaving a real integrity gap for broader retry and callback scenarios.

This is a narrow, evidence-based result: safe in the core flow, incomplete in the wider architecture.

---

## 5. Evidence Summary

- `Transaction.reference` is unique in the schema: `uq_transactions_reference`
- Wallet-funding credit is serialized with row locks and metadata gating
- Provider initialization sends a stable `Idempotency-Key` built from the transaction reference
- Duplicate webhook events are tracked in metadata and dropped
- `provider_reference` is indexed but not unique, which is the main persistent integrity gap

## 6. Audit Outcome

**Result:** No confirmed critical or exploitable double-credit vulnerability was found in the primary wallet-funding retry path reviewed in this phase.

**Residual risk:** A broader duplicate-provider or callback scenario remains possible in parts of the architecture because `provider_reference` uniqueness is not enforced by the database schema.

**Action:** The audited scope is considered safe enough to proceed with a review of higher-level business workflows, but not enough to claim the entire payment system is fully idempotent.

---

**Audit performed under strict read-only constraints. No application code was modified.**
