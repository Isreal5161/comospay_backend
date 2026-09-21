# IDOR REMEDIATION PLANNING AUDIT

**CosmozPay Backend**  
**Status**: PLANNING ONLY — NO CODE CHANGES AUTHORIZED  
**Date**: 2025-08-17  
**Classification**: ARCHITECTURE REVIEW

---

## A. EXECUTIVE SUMMARY

### Confirmed Vulnerabilities

**CRITICAL - 2 CONFIRMED IDOR VULNERABILITIES**

1. **SEC-IDOR-001**: Object-Level IDOR in Wallet Endpoints
2. **FIN-IDOR-001**: Object-Level IDOR in Transfer Operations

Both vulnerabilities stem from the **same root architectural issue**: Controllers accept user-scoped resource identifiers (user_id, wallet_id, sender_user_id) from request payloads without binding these to the authenticated user context from the JWT token.

### Root Cause Summary

```
Authentication Flow:
  JWT Token → AuthMiddleware → request.state.user (AuthenticatedUser) ✅

Authorization Flow:
  Controller receives request payload → No access to request.state.user ❌
  Service receives client-supplied user_id → Validates wallet.user_id == user_id ❌
  BUT: Never validates authenticated_user_id == supplied_user_id ❌
```

### Attack Pattern

```
User A authenticates (JWT issued for User A)
  ↓
Sends POST /wallets/fund with body:
  {
    "user_id": "User B UUID",
    "wallet_id": "User B Wallet UUID",
    ...
  }
  ↓
Controller receives payload, HAS NO ACCESS to request.state.user
  ↓
Service receives user_id=User B UUID
  ↓
Service validates: wallet_B.user_id == User B UUID ✅ (True)
  ↓
Operation proceeds: User A just funded User B's wallet ❌ IDOR VULNERABILITY
```

### Affected Endpoints

**Category: Wallet Operations** (10 endpoints)
- POST /wallets/fund
- POST /wallets/transfer
- GET /wallets/statement
- POST /wallets/pin (create)
- PUT /wallets/pin (update)
- POST /wallets/pin/verify
- POST /wallets/pin/reset
- GET /wallets/transactions (history)
- GET /wallets/transactions/{transaction_id} (details)
- GET /wallets/balance

**Category: Payment Operations** (2 endpoints)
- POST /payments (initialize)
- POST /payments/collect

**Category: VTU Services** (Multiple endpoints)
- POST /airtime/purchase
- POST /data/purchase
- POST /electricity/purchase
- POST /tv/subscribe
- POST /education/purchase
- POST /giftcard/trade

**Category: User Profile** (3 endpoints)
- GET /users/profile
- POST /users/profile (update)
- POST /users/profile/image (update/upload)

**Category: Virtual Accounts** (Multiple endpoints)
- All endpoints accepting user_id or wallet_id filters

**Category: Notifications** (Multiple endpoints)
- All user-specific notification endpoints

**Total Affected**: ~25-30 endpoints across the backend

### Production Blocking Status

**YES** — These vulnerabilities MUST be fixed before production deployment.

**WHY**: Complete financial and data integrity compromise. Any authenticated user can:
- Access other users' wallet data (statements, transactions, balances)
- Fund other users' wallets
- Initiate transfers from other users' wallets
- Modify other users' PINs
- Access other users' transaction history
- Modify other users' profile data
- Manipulate other users' VTU purchases (airtime, data, electricity, etc.)

---

## B. EXISTING AUTHENTICATION FLOW

### Current Implementation

**File**: `app/middleware/auth_middleware.py`

```
HTTP Request with JWT Token
  ↓
AuthMiddleware.dispatch() receives Request
  ↓
Extract Bearer token from Authorization header
  ↓
TokenService.validate_token() validates JWT signature & expiration
  ↓
TokenService.check_revoked_token() checks Redis for revocation status
  ↓
Validate token payload (required fields):
  - user_id or sub
  - email
  - role
  - expires_at (exp)
  - type == "access"
  ↓
Create AuthenticatedUser dataclass:
  - user_id: str | int (from payload)
  - email: str
  - role: str
  - token_type: str
  - expires_at, issued_at, subject
  ↓
Attach to request context:
  - request.state.user = AuthenticatedUser
  - request.state.auth_user = AuthenticatedUser
  - request.state.auth_payload = dict(claims)
  - request.state.token_type = "access"
  - request.state.is_authenticated = True
  ↓
Call next middleware/route handler
```

### Key Finding: Authentication Context IS Available

**IMPORTANT**: The authenticated user context IS correctly established in `request.state`.

The problem is NOT authentication.  
The problem is that controllers DO NOT RECEIVE the Request object to access this context.

### Comparison: AdminController vs WalletController

**AdminController** (CORRECT PATTERN):
```python
@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    request: Request,  # ← RECEIVES REQUEST
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user(UUID(user_id), request=request)

# Inside AdminController:
def _resolve_authenticated_admin_id(self, request: Request | None) -> UUID | None:
    if request is None:
        return None
    auth_user = getattr(request.state, "auth_user", None)
    raw_admin_id = getattr(auth_user, "user_id", None) if auth_user is not None else None
    if raw_admin_id is None:
        auth_payload = getattr(request.state, "auth_payload", None)
        if isinstance(auth_payload, dict):
            raw_admin_id = auth_payload.get("user_id") or auth_payload.get("sub")
    if raw_admin_id is None:
        return None
    try:
        return UUID(str(raw_admin_id))
    except (ValueError, TypeError):
        return None
```

**WalletController** (VULNERABLE PATTERN):
```python
@router.post("/fund")
async def fund_wallet(
    payload: WalletFundingRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    # NO REQUEST PARAMETER
    # Cannot access request.state.user
    return await controller.fund_wallet(payload)

# Inside WalletController:
async def fund_wallet(self, payload: WalletFundingRequest) -> dict[str, Any]:
    # No way to access authenticated user context
    return await self._execute(
        action="fund_wallet",
        handler=self.wallet_service.initialize_wallet_funding,
        payload={
            "user_id": payload.user_id,  # ← CLIENT-SUPPLIED, NOT VALIDATED
            "wallet_id": payload.wallet_id,  # ← CLIENT-SUPPLIED, NOT VALIDATED
            ...
        },
        success_message="Wallet funding request initiated successfully.",
    )
```

---

## C. EXISTING AUTHORIZATION FLOW

### Current Pattern: Service-Layer Ownership Checks

Services perform ownership validation, but it relies on trusting the client-supplied user_id.

**Example: PaymentManager._resolve_wallet()**

File: `app/services/payment/payment.py`, line 272

```python
async def _resolve_wallet(self, *, user_id: UUID, wallet_id: UUID | None) -> Wallet | None:
    if wallet_id is not None:
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is not None and wallet.user_id != user_id:
            raise ValidationException("Wallet ownership does not match the provided user.")
        return wallet
    return await self.wallet_repository.get_user_wallet(user_id=user_id)
```

**Analysis**:
- ✅ Correctly checks that `wallet.user_id == provided_user_id`
- ❌ But assumes `provided_user_id` is legitimate (comes from client request)
- ❌ Never verifies `authenticated_user_id == provided_user_id`

### Ownership Check Pattern Across Services

| Service | Current Check | Sufficient? | Problem |
|---------|---------------|-------------|---------|
| **WalletFundingService** | `if wallet.user_id != user_id: raise` | ❌ NO | Trusts client-supplied user_id |
| **WalletTransferService** | `if source_wallet.user_id != sender_user_id: raise` | ❌ NO | Trusts client-supplied sender_user_id |
| **WalletStatementService** | `if wallet.user_id != user_id: raise` | ❌ NO | Trusts client-supplied user_id |
| **PaymentManager** | `if wallet.user_id != user_id: raise` | ❌ NO | Trusts client-supplied user_id |
| **AirtimePurchaseService** | `wallet = await self._resolve_wallet(user_id, wallet_id)` | ❌ NO | Trusts client-supplied user_id |
| **Admin Services** | Checks admin_id from request context | ✅ YES | Extractsauthenticated admin_id from request.state |

---

## D. AFFECTED ENDPOINT INVENTORY

### WALLET OPERATIONS (High Risk)

| Endpoint | Method | Controller | Request Schema | Accepts | Vulnerable? | Reason |
|----------|--------|------------|---|---|---|---|
| /wallets | GET | WalletController | WalletInfoRequest | wallet_id | YES | No request.state.user access |
| /wallets/balance | GET | WalletController | WalletBalanceRequest | wallet_id | YES | No request.state.user access |
| /wallets/statement | GET | WalletController | WalletStatementRequest | user_id, wallet_id | YES | No request.state.user access; requires user_id in payload |
| /wallets/fund | POST | WalletController | WalletFundingRequest | user_id, wallet_id | YES | Client-supplied user_id, wallet_id not bound to auth context |
| /wallets/transfer | POST | WalletController | WalletTransferRequest | sender_user_id, sender_wallet_id, recipient_user_id, recipient_wallet_id | YES | sender_user_id not verified against authenticated user |
| /wallets/pin | POST | WalletController | TransactionPinRequest | user_id | YES | No request.state.user access |
| /wallets/pin | PUT | WalletController | TransactionPinRequest | user_id | YES | No request.state.user access |
| /wallets/pin/verify | POST | WalletController | TransactionPinVerificationRequest | user_id | YES | No request.state.user access |
| /wallets/pin/reset | POST | WalletController | TransactionPinResetRequest | user_id | YES | No request.state.user access |
| /wallets/transactions | GET | WalletController | TransactionHistoryRequest | user_id, wallet_id | YES | No request.state.user access |
| /wallets/transactions/{id} | GET | WalletController | TransactionDetailRequest | user_id, transaction_id | YES | No request.state.user access |

### PAYMENT OPERATIONS (High Risk)

| Endpoint | Method | Controller | Request Schema | Accepts | Vulnerable? | Reason |
|----------|--------|------------|---|---|---|---|
| /payments | POST | PaymentController | PaymentInitializeRequest | user_id (optional) | PARTIAL | Controller has fallback: `target_user_id = user_id or payload.user_id or self._resolve_user_id()` |
| /payments/collect | POST | PaymentController | PaymentCollectionRequest | user_id (optional) | PARTIAL | Same fallback pattern |
| /payments/verify | POST | PaymentController | PaymentVerificationRequest | reference | NO | No user context required (reference-based) |
| /payments/status/{reference} | GET | PaymentController | None (path param) | reference | NO | No user context required |

### VTU SERVICES (Medium Risk - but still affected)

| Endpoint | Service | Accepts | Vulnerable? | Reason |
|----------|---------|---------|---|---|
| /airtime/purchase | AirtimeController | user_id | YES | No request.state.user access |
| /data/purchase | DataController | user_id | YES | No request.state.user access |
| /electricity/pay | ElectricityController | user_id | YES | No request.state.user access |
| /tv/subscribe | TVController | user_id | YES | No request.state.user access |
| /education/pay | EducationController | user_id | YES | No request.state.user access |
| /giftcard/trade | GiftcardController | user_id | YES | No request.state.user access |

### USER PROFILE (Medium Risk)

| Endpoint | Method | Controller | Accepts | Vulnerable? | Reason |
|----------|--------|------------|---------|---|---|
| /users/profile | GET | UserController | Tries to resolve user_id from context | PARTIAL | Has fallback pattern but fallback logic needs verification |
| /users/profile | POST | UserController | user_id | PARTIAL | Has fallback pattern |
| /users/profile/image | POST | UserController | user_id | PARTIAL | Has fallback pattern |

### ADMIN OPERATIONS (Protected)

| Endpoint | Controller | Vulnerable? | Reason |
|----------|-----------|---|---|
| /admin/users/{user_id} | AdminController | NO | Receives Request; uses _resolve_authenticated_admin_id() |
| /admin/wallets/{wallet_id} | AdminController | NO | Receives Request; validates admin context |
| /admin/transactions/{id} | AdminController | NO | Receives Request |
| All /admin endpoints | AdminController | NO | All receive Request parameter; proper auth context binding |

---

## E. EXISTING OWNERSHIP CHECKS

### Wallet Funding Service

**File**: `app/services/wallet/funding.py`, line 63

```python
async def initialize_wallet_funding(
    self,
    *,
    user_id: UUID,
    wallet_id: UUID,
    ...
) -> dict[str, Any]:
    wallet = await self._get_wallet_and_validate_ownership(wallet_id, user_id)
    # ...
```

**Ownership Check Implementation** (line 376):

```python
async def _get_wallet_and_validate_ownership(self, wallet_id: UUID, user_id: UUID) -> Wallet:
    wallet = await self.wallet_repository.get_by_id(wallet_id)
    if not wallet:
        raise ValidationException("Wallet not found.")
    if wallet.user_id != user_id:
        raise WalletException("Wallet ownership mismatch.")
    return wallet
```

**Analysis**: ❌ INSUFFICIENT
- Validates internal consistency (wallet belongs to supplied user_id)
- Does NOT validate that authenticated_user == user_id

### Wallet Transfer Service

**File**: `app/services/wallet/transfer.py`, line 70

```python
async def transfer_between_users(
    self,
    *,
    sender_user_id: UUID,
    sender_wallet_id: UUID,
    recipient_user_id: UUID,
    recipient_wallet_id: UUID | None,
    ...
) -> dict[str, Any]:
    async with self._session_scope():
        source_wallet = await self._load_wallet_for_update(sender_wallet_id)
        if source_wallet is None:
            raise ValidationException("Wallet not found.")
        if source_wallet.user_id != sender_user_id:
            raise WalletException("Wallet ownership mismatch.")
        # ... proceeds with transfer ...
```

**Analysis**: ❌ INSUFFICIENT
- Verifies source wallet belongs to sender_user_id
- Does NOT verify that authenticated_user == sender_user_id
- Recipient wallet ownership verification relies on recipient_user_id from request

### Wallet Statement Service

**File**: `app/services/wallet/statement.py`, line 56

```python
async def get_wallet_statement(
    self,
    *,
    user_id: UUID,
    wallet_id: UUID,
    ...
) -> dict[str, Any]:
    wallet = await self._get_wallet_with_ownership_check(wallet_id=wallet_id, user_id=user_id)
    # ...
```

**Ownership Check** (line 457):

```python
async def _get_wallet_with_ownership_check(self, *, wallet_id: UUID, user_id: UUID) -> Wallet:
    wallet = await self.wallet_repository.get_by_id(wallet_id)
    if not wallet:
        raise ValidationException("Wallet not found.")
    if wallet.user_id != user_id:
        raise WalletException("You do not have access to this wallet.")
    return wallet
```

**Analysis**: ❌ INSUFFICIENT
- Validates wallet belongs to user_id
- Does NOT validate that authenticated_user == user_id

### Payment Manager

**File**: `app/services/payment/payment.py`, line 272

```python
async def _resolve_wallet(self, *, user_id: UUID, wallet_id: UUID | None) -> Wallet | None:
    if wallet_id is not None:
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is not None and wallet.user_id != user_id:
            raise ValidationException("Wallet ownership does not match the provided user.")
        return wallet
    return await self.wallet_repository.get_user_wallet(user_id=user_id)
```

**Analysis**: ❌ INSUFFICIENT
- Validates wallet belongs to user_id
- Does NOT validate that authenticated_user == user_id

---

## F. ROOT CAUSE ANALYSIS

### The Architectural Mistake

**Layer 1: Authentication (CORRECT)**
- Middleware validates JWT
- Populates request.state.user with authenticated context
- This part works correctly

**Layer 2: Route Handler (BROKEN)**
- Route handlers do NOT receive Request object
- Cannot access request.state.user
- Must pass all parameters to controller via injection

**Layer 3: Controller (BROKEN)**
- Controllers do NOT receive Request object
- Accept payload from route
- Pass payload fields directly to services
- No opportunity to bind user_id to authenticated context

**Layer 4: Service (PARTIALLY BROKEN)**
- Services perform internal consistency checks
- Verify wallet.user_id == provided_user_id
- But trust the provided_user_id comes from authenticated user
- No way to verify who supplied it

### Why It Happened

The current architecture separates concerns too strictly:
- Routes focus on HTTP binding
- Controllers focus on service orchestration
- Services focus on business logic

But this separation eliminated the opportunity for authorization checks that need both:
1. Authenticated user context (from JWT)
2. Requested resource (from payload)

---

## G. MINIMUM REMEDIATION DESIGN

### Design Principle: Defense-in-Depth

Fix the vulnerability at the EARLIEST OPPORTUNITY while preserving existing patterns.

### Option 1: ADD Request TO ROUTES (Recommended)

**Approach**: Pass Request object through to controllers where needed.

**Mechanism**:
```python
# Current (VULNERABLE):
@router.post("/fund")
async def fund_wallet(
    payload: WalletFundingRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.fund_wallet(payload)

# Proposed (SAFE):
@router.post("/fund")
async def fund_wallet(
    request: Request,  # ← ADD REQUEST
    payload: WalletFundingRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.fund_wallet(payload, request=request)
```

**Implementation in Controller**:
```python
async def fund_wallet(self, payload: WalletFundingRequest, request: Request | None = None) -> dict[str, Any]:
    # Extract authenticated user
    authenticated_user_id = self._get_authenticated_user_id(request)
    
    # Bind to authenticated context
    if authenticated_user_id is not None and payload.user_id != authenticated_user_id:
        raise AuthorizationException("Cannot perform operations on behalf of another user.")
    
    # Proceed with service call
    return await self._execute(
        action="fund_wallet",
        handler=self.wallet_service.initialize_wallet_funding,
        payload={"user_id": payload.user_id, "wallet_id": payload.wallet_id, ...},
        success_message="...",
    )
    
def _get_authenticated_user_id(self, request: Request | None) -> UUID | None:
    if request is None:
        return None
    auth_user = getattr(request.state, "auth_user", None)
    if auth_user is not None:
        return auth_user.user_id
    auth_payload = getattr(request.state, "auth_payload", None)
    if isinstance(auth_payload, dict):
        raw_id = auth_payload.get("user_id") or auth_payload.get("sub")
        if raw_id:
            try:
                return UUID(str(raw_id))
            except (ValueError, TypeError):
                pass
    return None
```

### Option 2: ADD USER DEPENDENCY EXTRACTOR (Alternative)

**Approach**: Create FastAPI Depends() helper that extracts authenticated user and passes to controllers.

```python
async def get_current_user_id(request: Request) -> UUID:
    auth_user = getattr(request.state, "auth_user", None)
    if auth_user is None:
        raise AuthenticationException("User not authenticated.")
    try:
        return UUID(str(auth_user.user_id))
    except (ValueError, TypeError):
        raise AuthenticationException("Invalid user_id in token.")

@router.post("/fund")
async def fund_wallet(
    payload: WalletFundingRequest,
    authenticated_user_id: UUID = Depends(get_current_user_id),
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.fund_wallet(payload, authenticated_user_id=authenticated_user_id)
```

### Comparison

| Aspect | Option 1 (Request) | Option 2 (Depends) |
|--------|-------|---------|
| **Consistency** | Different from current admin pattern (which uses Request) | New pattern in codebase |
| **Type Safety** | Request is loosely typed | UUID is explicit |
| **Error Handling** | Must extract in controller | Handled by FastAPI Depends |
| **Recommended** | ✅ YES | Alternative |

### Chosen Approach: Option 1

**Rationale**:
- Aligns with existing AdminController pattern
- Reuses _resolve_authenticated_user_id() helper  
- Minimal architectural change
- Preserves existing request/response patterns
- Already proven to work in AdminController

---

## H. SCHEMA CHANGES REQUIRED

### Analysis: Should client-supplied user_id be removed?

**Short Answer**: NO. DO NOT remove user_id from schemas.

**Why**:
1. Payment gateway webhooks need user_id mapping
2. Some admin operations intentionally work on behalf of users
3. Legitimate use cases require specifying resource ownership

**Solution**: Keep user_id in schemas, but validate it matches authenticated context.

### Affected Schemas

| Schema | Current Fields | Required Change |
|--------|---|---|
| **WalletStatementRequest** | user_id, wallet_id | VALIDATE: authenticated_user_id == user_id |
| **WalletFundingRequest** | user_id, wallet_id | VALIDATE: authenticated_user_id == user_id |
| **WalletTransferRequest** | sender_user_id, recipient_user_id, sender_wallet_id | VALIDATE: authenticated_user_id == sender_user_id |
| **TransactionPinRequest** | user_id | VALIDATE: authenticated_user_id == user_id |
| **TransactionHistoryRequest** | user_id, wallet_id | VALIDATE: authenticated_user_id == user_id |
| **PaymentInitializeRequest** | user_id (optional) | VALIDATE if provided: authenticated_user_id == user_id |
| **AirtimePurchaseRequest** | user_id | VALIDATE: authenticated_user_id == user_id |
| **UserProfileRequest** | Implicit (from auth context) | NO CHANGE |

### Webhook Special Case

Provider webhooks come from external systems (payment gateways) that are NOT authenticated via JWT.

**Webhook Authorization**: Verified by signature, not JWT.

**User Context**: Derived from webhook payload (e.g., `transaction.user_id`).

**No Changes Required** for webhook schemas.

---

## I. CONTROLLER CHANGES REQUIRED

### Change Pattern

All vulnerable controllers need:
1. Add `request: Request | None = None` parameter to affected methods
2. Extract authenticated user_id
3. Validate: `authenticated_user_id == payload.user_id` (or sender_user_id, etc.)
4. Raise AuthorizationException if mismatch

### Affected Controllers

**WalletController**
- fund_wallet() ← Add request parameter and validation
- transfer() ← Add request parameter and validation
- get_wallet_statement() ← Add request parameter and validation
- create_transaction_pin() ← Add request parameter and validation
- update_transaction_pin() ← Add request parameter and validation
- verify_transaction_pin() ← Add request parameter and validation
- reset_transaction_pin() ← Add request parameter and validation
- get_transaction_history() ← Add request parameter and validation
- get_transaction_details() ← Add request parameter and validation
- get_wallet() ← wallet_id only (no user_id in payload) - needs different approach
- get_wallet_balance() ← wallet_id only - needs different approach

**PaymentController**
- initialize_payment() ← Add validation if user_id provided
- collect_payment() ← Add validation if user_id provided

**AirtimeController, DataController, ElectricityController, TVController, EducationController, GiftcardController**
- All purchase/transaction methods ← Add request parameter and validation

**UserController**
- Already has partial pattern; needs standardization

**NotificationController**
- All user-specific methods ← Needs standardization

**VirtualAccountController** (if exists)
- All user/wallet-scoped methods ← Needs standardization

### Implementation Pattern

```python
class WalletController:
    # ... existing code ...
    
    async def fund_wallet(self, payload: WalletFundingRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet funding requests."""
        # Validate authenticated user matches payload
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is not None and payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot fund wallet for another user.")
        
        return await self._execute(
            action="fund_wallet",
            handler=self.wallet_service.initialize_wallet_funding,
            payload={
                "user_id": payload.user_id,
                "wallet_id": payload.wallet_id,
                # ... rest of payload ...
            },
            success_message="Wallet funding request initiated successfully.",
        )
    
    def _get_authenticated_user_id(self, request: Request | None) -> UUID | None:
        """Extract authenticated user_id from request context."""
        if request is None:
            return None
        auth_user = getattr(request.state, "auth_user", None)
        if auth_user is not None:
            try:
                return UUID(str(auth_user.user_id)) if auth_user.user_id else None
            except (ValueError, TypeError):
                return None
        auth_payload = getattr(request.state, "auth_payload", None)
        if isinstance(auth_payload, dict):
            raw_user_id = auth_payload.get("user_id") or auth_payload.get("sub")
            if raw_user_id:
                try:
                    return UUID(str(raw_user_id))
                except (ValueError, TypeError):
                    return None
        return None
```

### Special Case: wallet_id-only endpoints

**Problem**: Some endpoints accept only wallet_id (no user_id in schema).

Example: GET /wallets (WalletInfoRequest has only wallet_id)

**Solution**: Query the wallet from repository, then validate authenticated user matches wallet.user_id.

```python
async def get_wallet(self, payload: WalletInfoRequest, request: Request | None = None) -> dict[str, Any]:
    """Handle wallet information retrieval requests."""
    # Validate authenticated user owns the wallet
    wallet = await self.wallet_service.get_wallet(wallet_id=payload.wallet_id)
    authenticated_user_id = self._get_authenticated_user_id(request)
    
    if authenticated_user_id is not None and wallet.user_id != authenticated_user_id:
        raise AuthorizationException("You do not have access to this wallet.")
    
    return await self._execute(
        action="get_wallet",
        handler=self.wallet_service.get_wallet,
        payload={"wallet_id": payload.wallet_id},
        success_message="Wallet retrieved successfully.",
    )
```

---

## J. SERVICE CHANGES REQUIRED

### Analysis: Should service layer ownership checks be modified?

**Short Answer**: NO. Keep existing service layer checks as-is.

**Why**:
1. Services are not responsible for authentication
2. Services should focus on business logic
3. Defense-in-depth: Keep redundant checks in place
4. Existing pattern works if controller layer is fixed

### Service Layer Principle

Services will continue to validate:
- Wallet belongs to provided user_id
- Transaction belongs to provided user_id
- Resource ownership consistency

But controllers will now guarantee the provided user_id matches the authenticated user.

### No Service Code Changes Needed

The existing ownership validation in services is correct and sufficient.

---

## K. MIDDLEWARE CHANGES REQUIRED

### Analysis: Should we add middleware to enforce authorization?

**Short Answer**: NO. Do NOT add new middleware.

**Why**:
1. Authorization is resource-specific (depends on which wallet, transaction, etc.)
2. Different endpoints have different rules
3. Existing patterns (controller + service) already handle this
4. Adding auth middleware would duplicate controller-level checks

### AuthMiddleware Status

AuthMiddleware correctly:
- ✅ Validates JWT
- ✅ Populates request.state.user
- ✅ Checks token revocation
- ✅ Validates payload structure

No changes required to AuthMiddleware.

---

## L. REPOSITORY CHANGES REQUIRED

### Analysis: Should repository methods enforce authorization?

**Short Answer**: NO. Repositories are persistence-only.

**Why**:
1. Repositories do not have authorization context
2. Authorization decisions should be made by services/controllers
3. Repositories should be agnostic about who is calling

### Status

No repository changes required.

---

## M. DATABASE CHANGES REQUIRED

### Analysis: Should database schema support authorization?

**Short Answer**: NO. Database structure is sufficient.

**Why**:
1. Wallet.user_id already tracks ownership
2. Transaction.user_id already tracks user
3. All relationships are properly modeled

### Status

No database migrations required.

---

## N. PAYMENT GATEWAY IMPACT

### Confirmed: Webhook Flow is PROTECTED

Payment gateway webhooks do NOT use JWT and do NOT go through AuthMiddleware.

**Webhook Authorization**: Verified by provider-specific signature validation.

**Current Webhook Flow**:
```
Provider sends webhook
    ↓
Webhook verifies provider signature (independent of JWT)
    ↓
Webhook routes to webhookController or webhook_routes
    ↓
Controller/handler verifies signature
    ↓
Transaction is located/created based on provider reference
    ↓
Wallet is credited based on transaction.user_id
```

**Impact of IDOR Remediation**: NONE

Webhook processing does not change because:
1. Webhooks bypass regular authentication (they're provider-to-backend)
2. Webhooks verify provider identity via signature
3. Webhooks don't send JWT tokens
4. Authorization is based on provider signature, not user authentication

### Admin Operations on Behalf of Users

Admin controllers already receive Request and extract authenticated admin context.

**No changes required** for admin operations.

---

## O. ADMIN IMPACT

### Confirmed: Admin Operations Already Protected

AdminController patterns already:
1. ✅ Receive Request
2. ✅ Extract authenticated admin_id
3. ✅ Log admin actions
4. ✅ Enforce admin-only access to resources

**Example**:
```python
async def manage_user(self, user_id: UUID, payload: UserManagementRequest, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
    """Handle user management requests."""
    target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
    # Ensures admin_id comes from authenticated context
    return await self._execute(
        action="manage_user",
        handler=self._resolve_handler("manage_user"),
        payload={
            "admin_id": target_admin_id,
            "user_id": user_id,
            ...
        },
        success_message="User management action completed successfully.",
    )
```

**Impact of IDOR Remediation**: NONE

Admin operations continue to work as designed.

---

## P. SECURITY TEST MATRIX

### Test Suite Structure

All tests must verify BOTH:
1. **Happy path**: Legitimate user can access their own resources
2. **Rejection path**: User CANNOT access another user's resources

### Required Tests

#### TEST 1: Own wallet access ✅
**Scenario**: User A authenticated → requests User A's wallet  
**Expected**: SUCCESS (200/201)  
**Assertion**: Wallet data returned with User A's data

#### TEST 2: Cross-user wallet access ❌
**Scenario**: User A authenticated → requests User B's wallet_id  
**Expected**: REJECTION (403 Forbidden or similar)  
**Assertion**: AuthorizationException raised; no wallet data leaked

#### TEST 3: Own wallet statement ✅
**Scenario**: User A authenticated → requests statement for User A's wallet  
**Expected**: SUCCESS (200)  
**Assertion**: Transaction history returned for User A's wallet only

#### TEST 4: Cross-user wallet statement ❌
**Scenario**: User A authenticated → requests statement for User B's wallet  
**Expected**: REJECTION (403)  
**Assertion**: Access denied; no statement data leaked

#### TEST 5: Own wallet funding ✅
**Scenario**: User A authenticated → funds User A's wallet  
**Expected**: SUCCESS (200/201)  
**Assertion**: Funding initiated for User A's wallet; transaction created

#### TEST 6: Cross-user wallet funding ❌
**Scenario**: User A authenticated → attempts to fund User B's wallet  
**Expected**: REJECTION (403)  
**Assertion**: Authorization failed; User B's wallet not modified

#### TEST 7: Own-to-own transfer ✅
**Scenario**: User A authenticated → transfers from User A's wallet to User A's wallet  
**Expected**: SUCCESS (200) or BUSINESS RULE REJECTION (e.g., "cannot transfer to self")  
**Assertion**: Either transfer completes or legitimate business rule applies

#### TEST 8: Own-to-other transfer ✅
**Scenario**: User A authenticated → transfers from User A's wallet to User B's wallet  
**Expected**: SUCCESS (200/201)  
**Assertion**: Transfer initiated; funds moved from A to B

#### TEST 9: Unauthorized transfer (sender) ❌
**Scenario**: User A authenticated → attempts transfer from User B's wallet to User C's wallet  
**Expected**: REJECTION (403)  
**Assertion**: Authorization failed; no transfer initiated

#### TEST 10: Unauthorized transfer (recipient hijack) ❌
**Scenario**: User A authenticated → initiates transfer TO User A's wallet FROM User B  
**Expected**: REJECTION (403) at sender authorization check  
**Assertion**: Sender (User B) not authenticated as User A

#### TEST 11: Own PIN management ✅
**Scenario**: User A authenticated → creates/updates PIN for User A  
**Expected**: SUCCESS (201/200)  
**Assertion**: PIN set for User A's account

#### TEST 12: Cross-user PIN manipulation ❌
**Scenario**: User A authenticated → attempts to create PIN for User B  
**Expected**: REJECTION (403)  
**Assertion**: PIN not modified for User B

#### TEST 13: Own transaction details ✅
**Scenario**: User A authenticated → requests details for User A's transaction  
**Expected**: SUCCESS (200)  
**Assertion**: Transaction details returned; belongs to User A

#### TEST 14: Cross-user transaction details ❌
**Scenario**: User A authenticated → requests details for User B's transaction  
**Expected**: REJECTION (403)  
**Assertion**: Access denied; no transaction details leaked

#### TEST 15: Own payment initialization ✅
**Scenario**: User A authenticated → initializes payment for User A  
**Expected**: SUCCESS (201)  
**Assertion**: Payment reference created; associated with User A

#### TEST 16: Cross-user payment initialization ❌
**Scenario**: User A authenticated → attempts to initialize payment for User B  
**Expected**: REJECTION (403)  
**Assertion**: Payment not created for User B

#### TEST 17: Own airtime purchase ✅
**Scenario**: User A authenticated → purchases airtime charged to User A's wallet  
**Expected**: SUCCESS (200/201)  
**Assertion**: Transaction created; User A's wallet debited

#### TEST 18: Cross-user airtime purchase ❌
**Scenario**: User A authenticated → attempts to purchase airtime using User B's wallet  
**Expected**: REJECTION (403)  
**Assertion**: Purchase not initiated; User B's wallet not debited

#### TEST 19: Own profile access ✅
**Scenario**: User A authenticated → requests User A's profile  
**Expected**: SUCCESS (200)  
**Assertion**: User A's profile returned; full details visible

#### TEST 20: Cross-user profile access ❌
**Scenario**: User A authenticated → requests User B's profile  
**Expected**: REJECTION (403) or returns limited public data (business rule dependent)  
**Assertion**: Sensitive User B data not exposed

#### TEST 21: Own profile update ✅
**Scenario**: User A authenticated → updates User A's profile  
**Expected**: SUCCESS (200)  
**Assertion**: Profile updated with User A's new data

#### TEST 22: Cross-user profile update ❌
**Scenario**: User A authenticated → attempts to update User B's profile  
**Expected**: REJECTION (403)  
**Assertion**: User B's profile not modified

#### TEST 23: Provider webhook (admin-bypassed) ✅
**Scenario**: Provider sends webhook with valid signature → wallet credited  
**Expected**: SUCCESS (200)  
**Assertion**: Webhook processed; user wallet credited based on transaction.user_id

#### TEST 24: Unauthenticated request ❌
**Scenario**: No JWT token → requests wallet data  
**Expected**: REJECTION (401 Unauthorized)  
**Assertion**: Blocked by AuthMiddleware

#### TEST 25: Invalid JWT token ❌
**Scenario**: Malformed JWT → requests wallet data  
**Expected**: REJECTION (401 Unauthorized)  
**Assertion**: Blocked by AuthMiddleware

#### TEST 26: Revoked JWT token ❌
**Scenario**: Valid JWT that has been revoked → requests wallet data  
**Expected**: REJECTION (401 Unauthorized)  
**Assertion**: Blocked by revocation check

#### TEST 27: Forged user_id in payload ❌
**Scenario**: User A token + requests with user_id=User B  
**Expected**: REJECTION (403)  
**Assertion**: Mismatch detected; request rejected

#### TEST 28: JWT user mismatch - statement ❌
**Scenario**: Valid JWT for User A + statement request for User B's wallet  
**Expected**: REJECTION (403)  
**Assertion**: Authorization check catches mismatch

#### TEST 29: JWT user mismatch - transfer ❌
**Scenario**: Valid JWT for User A + transfer from User B's wallet  
**Expected**: REJECTION (403)  
**Assertion**: Sender authorization fails

#### TEST 30: Idempotent request retry ✅
**Scenario**: User A sends same request twice with Idempotency-Key  
**Expected**: First returns 200/201; Second returns same response  
**Assertion**: Idempotency working; financial consistency maintained

### Test Organization

**Test File Structure**:
```
tests/
  test_wallet_authorization.py        # Tests 1-14
  test_payment_authorization.py       # Tests 15-16
  test_vtu_authorization.py           # Tests 17-18
  test_user_profile_authorization.py  # Tests 19-22
  test_webhook_authorization.py       # Test 23
  test_authentication.py              # Tests 24-26
  test_authorization_regression.py    # Tests 27-30
```

---

## Q. REGRESSION RISKS

### Risk 1: Admin Operations Broken

**Scenario**: Admin operations rely on separate admin_id parameter; changes to regular user endpoints might accidentally affect admin context.

**Mitigation**: Admin routes and controllers are separate from user routes. Changes to WalletController do not affect AdminController.

**Verification**: Run full admin test suite alongside user authorization tests.

### Risk 2: Payment Gateway Webhooks Blocked

**Scenario**: If webhook controller is incorrectly modified to require request.state.user, webhooks will fail (webhooks have no JWT).

**Mitigation**: Webhooks are processed separately; webhook handlers do NOT go through standard authorization.

**Verification**: Webhook controller is EXPLICITLY NOT MODIFIED; test webhook processing separately.

### Risk 3: Service-Layer Ownership Checks Conflict

**Scenario**: If controller adds owner validation AND service has redundant checks with different error messages, debugging becomes difficult.

**Mitigation**: Service-layer checks remain unchanged; controller-layer adds earlier check. Both checks pass for legitimate requests.

**Verification**: Ensure error messages are distinct; controller raises AuthorizationException, services raise WalletException.

### Risk 4: Unauthenticated Endpoints Broken

**Scenario**: Some endpoints should be public (e.g., payment verification, KYC initiation). Adding request.state.user validation breaks them.

**Mitigation**: Controller validation only applies to user-scoped operations. Public endpoints explicitly excluded.

**Verification**: Audit endpoint paths; public endpoints must not require user ownership binding.

### Risk 5: Optional user_id Parameters Mishandled

**Scenario**: Some payloads have optional user_id fields; controllers must distinguish "not provided" from "provided but wrong".

**Mitigation**: Controller logic:
```python
if payload.user_id is None:
    # Optional: derive from authenticated context
    payload.user_id = authenticated_user_id
elif payload.user_id != authenticated_user_id:
    # Provided but mismatched: reject
    raise AuthorizationException(...)
```

**Verification**: Test endpoints with optional user_id both with and without providing value.

### Risk 6: Migration Path Issues

**Scenario**: If old clients send requests with wrong user_id, they will suddenly fail.

**Mitigation**: This is a SECURITY FIX, not a backward-compatible enhancement. Breaking old vulnerable clients is intended.

**Verification**: Client SDK must be updated before backend deployment.

### Risk 7: Admin Impersonation

**Scenario**: If admin operations still allow arbitrary user_id in payloads, admins could impersonate users.

**Mitigation**: Admin controller already enforces that operations are logged and attributed to authenticated admin. Separate code path.

**Verification**: Admin operations still include admin_id in logs; audit trail preserved.

### Risk 8: Session/Token Issues

**Scenario**: If request.state.user is not populated (e.g., middleware failure), controllers might have None values.

**Mitigation**: Controller validation:
```python
if authenticated_user_id is None:
    raise AuthenticationException("User not authenticated")
```

**Verification**: AuthMiddleware properly validates all tokens before request reaches route handlers.

---

## R. IMPLEMENTATION SEQUENCE

### Phase 1: Preparation (No Code Changes)

**Duration**: 1-2 hours

1. Review this audit with team
2. Align on remediation approach (Option 1 chosen)
3. Identify primary vs. secondary endpoints
4. Plan testing strategy

### Phase 2: Routes Modification (Primary Endpoints)

**Duration**: 4-6 hours

**Endpoints**:
- /wallets/fund
- /wallets/transfer
- /wallets/statement
- /wallets/pin (all variants)
- /wallets/transactions
- /payments (initialize, collect)

**Changes**:
1. Add `request: Request` parameter to route handlers
2. Pass request to controller methods

### Phase 3: Controllers Modification (Primary Endpoints)

**Duration**: 6-8 hours

**Controllers**:
- WalletController (primary methods)
- PaymentController (primary methods)

**Changes**:
1. Add `request: Request | None` parameter to methods
2. Implement `_get_authenticated_user_id(request)` helper
3. Add authorization validation at method entry
4. Raise AuthorizationException on mismatch

### Phase 4: Controllers Modification (Secondary Endpoints)

**Duration**: 4-6 hours

**Controllers**:
- AirtimeController
- DataController
- ElectricityController
- TVController
- EducationController
- GiftcardController
- UserController (standardize existing pattern)
- NotificationController
- VirtualAccountController (if exists)

**Changes**: Same pattern as Phase 3

### Phase 5: Testing - Unit Tests

**Duration**: 8-10 hours

**Tests**:
- Write Tests 1-10 (wallet operations)
- Write Tests 11-14 (PIN management)
- Write Tests 15-16 (payments)
- Write Tests 17-18 (VTU)
- All tests must verify rejection behavior

### Phase 6: Testing - Integration Tests

**Duration**: 6-8 hours

**Tests**:
- Write Tests 19-22 (profile operations)
- Write Tests 23 (webhooks still work)
- Write Tests 24-26 (authentication)
- Write Tests 27-30 (edge cases)

### Phase 7: Regression Testing

**Duration**: 6-8 hours

**Coverage**:
- Admin operations unchanged
- Webhook processing unchanged
- Service layer unchanged
- All existing tests still pass

### Phase 8: Security Verification

**Duration**: 4-6 hours

**Activities**:
1. Penetration testing (attempt IDOR exploitation)
2. Payload manipulation testing (forge user_id values)
3. Cross-user access attempts
4. Admin operation verification

### Phase 9: Documentation & Deployment

**Duration**: 2-4 hours

**Deliverables**:
1. Update API documentation
2. Client SDK changelog
3. Deployment instructions
4. Rollback plan

### Total Estimated Effort

**Development**: 20-28 hours  
**Testing**: 20-26 hours  
**Security verification**: 4-6 hours  
**Documentation**: 2-4 hours  

**TOTAL**: 46-64 hours (6-8 engineer-weeks)

---

## S. ARCHITECTURE COMPLIANCE CHECK

### ✅ Clean Architecture Preserved

**Concern**: Will IDOR fix break clean architecture?

**Verification**:
- ✅ Controllers remain HTTP boundaries (receive Request, return response)
- ✅ Services remain business logic (no HTTP concerns)
- ✅ Repositories remain persistence-only
- ✅ No middleware added
- ✅ No new architectural layers introduced

### ✅ SOLID Principles Preserved

**Single Responsibility**:
- ✅ Controllers: HTTP + Authorization binding
- ✅ Services: Business logic + internal validation
- ✅ Repositories: Data access

**Open/Closed**: ✅ New behavior via controller additions, not modification of core logic

**Liskov Substitution**: ✅ Service interfaces unchanged

**Interface Segregation**: ✅ AuthenticatedUser interface already exists in AuthMiddleware

**Dependency Inversion**: ✅ Controllers still depend on service abstractions

### ✅ Business Logic Preserved in Services

Services continue to:
- ✅ Perform wallet operations
- ✅ Validate ownership (internal consistency)
- ✅ Enforce business rules (limits, locks, etc.)
- ✅ Manage transactions
- ✅ Orchestrate providers

No business logic moves to controllers.

### ✅ ProviderManager Unchanged

- ✅ Payment gateway integrations untouched
- ✅ Provider failover mechanisms unchanged
- ✅ Webhook processing unchanged

### ✅ Wallet Architecture Unchanged

- ✅ WalletService facade preserved
- ✅ Internal service composition unchanged
- ✅ WalletManager, WalletFundingService, WalletTransferService all unchanged
- ✅ Transaction processing unchanged

### ✅ Payment Architecture Unchanged

- ✅ PaymentService facade preserved
- ✅ PaymentManager unchanged
- ✅ Provider orchestration unchanged
- ✅ Webhook verification unchanged

### ✅ Financial Integrity Mechanisms Preserved

- ✅ Transaction locking
- ✅ Wallet balance integrity checks
- ✅ Ledger consistency
- ✅ Provider reference uniqueness (SCAL-006)
- ✅ Idempotency protection

---

## T. APPROVAL CHECKPOINT

### STATUS

**PLANNING COMPLETE — NO CODE CHANGES MADE**

This audit is a READ-ONLY DESIGN DOCUMENT ONLY.

**All findings are architecture-level; no implementation has occurred.**

---

### FILES THAT WOULD NEED CHANGES

**Route Files** (Add `request: Request` parameter):
- `app/routes/wallet_routes.py`
- `app/routes/payment_routes.py`
- `app/routes/airtime_routes.py`
- `app/routes/data_routes.py`
- `app/routes/electricity_routes.py`
- `app/routes/tv_routes.py`
- `app/routes/education_routes.py`
- `app/routes/giftcard_routes.py`
- `app/routes/user_routes.py`
- `app/routes/notification_routes.py`

**Controller Files** (Add authorization validation):
- `app/controllers/wallet_controller.py`
- `app/controllers/payment_controller.py`
- `app/controllers/airtime_controller.py`
- `app/controllers/data_controller.py`
- `app/controllers/electricity_controller.py`
- `app/controllers/tv_controller.py`
- `app/controllers/education_controller.py`
- `app/controllers/giftcard_controller.py`
- `app/controllers/user_controller.py`
- `app/controllers/notification_controller.py`

**Test Files** (Create new):
- `tests/test_wallet_authorization.py`
- `tests/test_payment_authorization.py`
- `tests/test_vtu_authorization.py`
- `tests/test_user_profile_authorization.py`
- `tests/test_webhook_authorization.py`
- `tests/test_authentication.py`
- `tests/test_authorization_regression.py`

---

### FILES THAT MUST NOT CHANGE

**Protected - Do NOT Modify**:
- `app/middleware/auth_middleware.py` (Authentication works correctly)
- `app/middleware/admin_middleware.py` (Admin protection works correctly)
- `app/services/wallet/` (Business logic untouched)
- `app/services/payment/` (Business logic untouched)
- `app/services/vtu/` (Business logic untouched)
- `app/repositories/` (Persistence layer untouched)
- `app/models/` (Database models untouched)
- `app/integrations/payments/` (Payment gateway integrations untouched)
- `app/jobs/` (Background jobs untouched)
- `app/utils/` (Utility functions untouched)

---

### RECOMMENDED FIX

**Approach**: Option 1 (Add Request to Routes/Controllers)

**Rationale**:
1. ✅ Aligns with existing AdminController pattern
2. ✅ Minimal architectural disruption
3. ✅ Reuses proven authentication context extraction
4. ✅ Clear authorization semantics
5. ✅ Defense-in-depth (controller + service checks)

**Implementation Order**:
1. Wallet endpoints (highest risk, most critical)
2. Payment endpoints
3. VTU endpoints
4. User profile endpoints
5. Notification endpoints
6. Other secondary endpoints

**Deployment**:
- Create feature branch
- Implement in phases (with testing after each phase)
- Comprehensive security testing before merge
- Client SDK update required (breaking change)
- Coordinate release with frontend team

---

### SECURITY RISK AFTER FIX

**After Implementing Fix**: 

**Remaining IDOR Risk**: ELIMINATED for authenticated user endpoints

**Residual Risks** (outside scope of IDOR):
- Admin impersonation (if admin operations misused) → Mitigated by admin audit trail
- Webhook spoofing (if signature verification bypassed) → Mitigated by provider signature validation
- Brute force wallet_id guessing → Existing rate limiting middleware
- Payment transaction hijacking → Mitigated by provider reference uniqueness
- Session hijacking → Mitigated by JWT revocation mechanism

---

### REGRESSION TESTS REQUIRED

**Before Deployment**:
1. ✅ All wallet operations work for authenticated user's own resources
2. ✅ All wallet operations rejected for other users' resources
3. ✅ All payment operations work correctly
4. ✅ All VTU operations isolated per user
5. ✅ Admin operations unchanged and functional
6. ✅ Webhook processing unchanged and functional
7. ✅ Authentication (JWT validation) unchanged
8. ✅ Rate limiting unchanged
9. ✅ Idempotency unchanged
10. ✅ Financial transaction integrity unchanged
11. ✅ Provider failover unchanged
12. ✅ Error handling and logging functional

---

## WAIT FOR EXPLICIT APPROVAL

**This audit is complete. No implementation should proceed without:**

1. ✅ Security team review and approval
2. ✅ Backend team review and approval
3. ✅ DevOps/deployment team review
4. ✅ Frontend team coordination (breaking API change)
5. ✅ Product/business stakeholder sign-off

**Next Steps**:
- Share this audit document with stakeholders
- Schedule review meeting
- Collect approval decisions
- Once approved, proceed with Phase 1 preparation

**DO NOT MODIFY CODE UNTIL APPROVED.**

---

**End of IDOR Remediation Planning Audit**

**Document Status**: FINAL - PLANNING ONLY  
**Classification**: INTERNAL - SECURITY SENSITIVE  
**Next Review**: Post-approval, before implementation begins
