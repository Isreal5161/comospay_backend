# CosmozPay Remaining Security & Scalability Findings Audit

Date: 2026-08-16
Scope: Strict read-only audit of the remaining findings from the previous full backend validation report.

---

## 1. Executive Summary

This audit re-evaluated the earlier findings in the previous validation report using a stricter standard: each issue had to be traced to actual code paths, validated against the current repository, and classified as confirmed vulnerability, valid design weakness, residual risk, false positive, or insufficient evidence.

The major result is that the previous high-severity concern is not a confirmed exploit in the current audited financial flow. It is better classified as a valid design weakness or residual risk because there is a material integrity concern around provider identity reuse, but the code paths reviewed do not show an actual double-credit/double-debit exploit in the current wallet and transaction lifecycle.

The previous medium findings are mostly not code vulnerabilities:
- the permissive default configuration is guarded by production-time validation and explicit tests;
- the deprecated FastAPI startup/shutdown hooks are compatibility warnings, not an active security bug;
- lack of configured coverage is a quality assurance gap, not a direct security or scalability vulnerability.

The final result is therefore:
- no confirmed security vulnerabilities in the current audited code paths;
- one genuine design weakness with a residual financial-integrity risk;
- two findings that are better treated as false positives;
- one quality gap (coverage configuration) that is a valid design weakness rather than an exploit.

---

## 2. Original Findings Investigated

From the previous validation report, the remaining findings were:

1. HIGH-001: `provider_reference` uniqueness is not enforced at the database layer.
2. MEDIUM-001: permissive default CORS/trusted host configuration is unsafe unless env is set correctly.
3. MEDIUM-002: deprecated FastAPI lifecycle hooks remain in use.
4. MEDIUM-003: coverage is not configured in the active environment.
5. LOW findings: deprecated status constants and operational dependency on environment discipline.

These were examined in order, with the actual code and test behavior checked against the repository state.

---

## 3. HIGH-001 Investigation

### Original finding

`provider_reference` uniqueness is not enforced at the database layer.

### Exact source

- [app/models/transaction.py](app/models/transaction.py)
- [app/services/wallet/funding.py](app/services/wallet/funding.py)

Relevant exact behavior:
- The model defines an index on `(provider_name, provider_reference)` but no unique constraint on `provider_reference`.
- `WalletFundingService._ensure_provider_reference_is_unique()` performs an application-level query check using `session.query(Transaction).filter(Transaction.provider_reference == provider_reference)` before continuing.

### Execution path

Route / Request → Wallet funding service → `_ensure_provider_reference_is_unique()` → repository session query → transaction lookup by `provider_reference` → wallet crediting path if accepted.

This logic is used when a wallet funding request is initialized and before a provider reference is accepted.

### Root cause

The design uses a database index for query performance but not a database-enforced uniqueness constraint for provider identity. The uniqueness guard exists only as an application-level check in one service method and does not protect all transaction paths unless they call the same function.

### Actual impact

The actual impact is limited by review of the current code paths:
- the wallet transaction record itself still uses a unique internal `transactions.reference` value;
- wallet crediting is gated by `transaction.status` and `wallet_credit_applied` metadata in the reviewed wallet-funding and payment verification paths;
- the same logical transaction is not being double credited in the audited code paths because each wallet credit path re-fetches the transaction under row lock and refuses duplicates.

This does not create a confirmed wallet double-credit exploit in the current flow.

### Exploitability

The issue is not proven reproducible as a wallet-financial exploit in the current architecture.

What is plausible is a broader external-provider deduplication gap:
- if the same provider reference is reused for multiple internal transaction records,
- and if different code paths or provider callbacks accept the same external identity,
- then the app can attach multiple internal transaction rows to one external identity.

That creates a real integrity problem, but not a proven double-credit issue in the reviewed wallet pathway.

### Existing mitigations

Existing mitigations include:
- unique internal business reference in [app/models/transaction.py](app/models/transaction.py)
- `WITH FOR UPDATE` row-locking in [app/repositories/transaction_repository.py](app/repositories/transaction_repository.py) and [app/repositories/wallet_repository.py](app/repositories/wallet_repository.py)
- transaction status guards in [app/services/wallet/funding.py](app/services/wallet/funding.py)
- metadata-based idempotency in payment verification and webhook flows
- duplicate webhook filtering in [app/services/payment/webhook.py](app/services/payment/webhook.py)

These protections neutralize the classic double-credit race in the audited code, but they do not eliminate the broader provider-reference naming weakness.

### Reproduction / validation

The issue cannot be proven as an active exploit without a precise scenario of multiple internal transaction records sharing the same external provider reference under a real provider callback path. The repository code does not show a deterministic exploit path under the current tests.

### Tests

- Full suite: 167 passed
- Relevant targeted tests: 15 passed
- No direct test currently asserts uniqueness of provider_reference across transactions.

### Final classification

VALID DESIGN WEAKNESS

### Correct severity

HIGH

### Recommended remediation

- Add a database-enforced uniqueness rule or a sector-specific dedupe strategy for provider identity where the financial contract requires one-to-one mapping between external provider reference and internal business transaction.
- Keep the application-level check, but treat it as a second line of defense rather than the only enforcement point.

---

## 4. MEDIUM-001 Investigation

### Original finding

Permissive default CORS and trusted-host configuration is unsafe if environment settings are not configured correctly.

### Exact source

- [app/config/settings.py](app/config/settings.py)
- [app/main.py](app/main.py)
- [tests/test_cors_production_wildcard.py](tests/test_cors_production_wildcard.py)

Relevant exact behavior:
- Default values in settings are `cors_allow_origins=[