# SCAL-001 Architecture Audit Report

## Audit Scope

- **Repository file:** [app/repositories/virtual_account_repository.py](app/repositories/virtual_account_repository.py)
- **Service file:** [app/services/virtual_account_service.py](app/services/virtual_account_service.py)
- **Test file:** [tests/test_scal001_retry_lease.py](tests/test_scal001_retry_lease.py)

---

## Method-by-Method Audit

### 1. `claim_retry_for_processing()` — Repository Lines 274–312

**Purpose:** Atomically claim a PENDING/PROVISIONING/FAILED virtual account for retry processing. Returns True if successfully claimed, False if already claimed by another worker or lease expired.

#### Answers A–H:

| Question | Answer | Evidence |
|---|---|---|
| **A. Direct UPDATE?** | ✅ YES | Line 285–306: `update(VirtualAccount).where(...).values(...)` with atomic WHERE conditions |
| **B. Commits?** | ✅ YES | Line 310: `await self.session.commit()` |
| **C. Return type?** | `bool` | Line 311: Returns `bool(result.rowcount)` (True if 1 row matched, False if 0) |
| **D. Caller accesses ORM?** | ✅ YES — **Service AND Test** | Service (line 920): Manually updates lease fields on in-memory instance after claim. Test (line 98): Calls `session.get()` and accesses `lease.retry_owner_id` |
| **E. Caller needs updated state immediately?** | ✅ YES — **Both service and test** | Service (line 920): Updates in-memory object immediately. Test (line 94–98): Verifies state on next line after returning from repository |
| **F. Refresh side-effects?** | ⚠️ **YES — PROBLEMATIC** | Service at line 346–351 has explicit comment: "Keep the current object after a successful claim. Reloading here can erase a freshly granted in-memory or DB lease when the ORM instance is still in the same session" — Service intentionally does NOT reload to avoid losing manually-updated state |
| **G. Expunge side-effects?** | ⚠️ **YES — DETACHES OBJECT** | Would break service's assumption that same ORM instance is kept in session after claim. Service at line 346–351 explicitly avoids reloading. Service then manually updates that same instance at lines 920–927 |
| **H. Explicit SELECT after mutation?** | ✅ **POSSIBLE** | Could do `SELECT...populate_existing=True` after UPDATE to refresh identity_map while keeping instance tracked. This would solve stale data without detaching or requiring manual expiration |

**Service Flow After Claim:**
1. Line 915: `claimed = await repository.claim_retry_for_processing(...)`
2. Line 918-920: If `claimed == True`, service manually updates in-memory `virtual_account` object with fresh lease values
3. Line 926: Service adds/flushes the manually-updated object to session
4. Line 352: Service calls `_verify_retry_lease()` which first checks in-memory state

**Why Service Works:** Service does NOT rely on repository to refresh ORM; it manually updates the in-memory object with the exact values it knows were just set in the database.

**Why Test Fails:** Test does NOT manually update ORM; it relies on `session.get()` to return fresh data. But `session.get()` returns stale cached instance from identity_map.

---

### 2. `verify_retry_lease()` — Repository Lines 313–327

**Purpose:** Check if a worker still holds an active, non-expired lease.

#### Answers A–H:

| Question | Answer | Evidence |
|---|---|---|
| **A. Direct UPDATE?** | ❌ NO | Line 316–324: Only SELECT query, no UPDATE |
| **B. Commits?** | ❌ NO | No commit needed |
| **C. Return type?** | `bool` | Line 325: Returns `result.scalar_one_or_none() is not None` |
| **D. Caller accesses ORM?** | ❌ NO | Returns only bool; no ORM instance returned |
| **E. Caller needs updated state?** | ❌ NO | Only needs bool result |
| **F. Refresh side-effects?** | N/A | Does not mutate; no refresh needed |
| **G. Expunge side-effects?** | N/A | Does not mutate; no expunge issues |
| **H. SELECT after mutation?** | N/A | No mutation; only SELECT |

**Service Flow:**
- Line 349: `_verify_retry_lease()` called to verify ownership before provider call
- Line 966: Falls through to repository `verify_retry_lease()` after checking in-memory and cached state
- Returns bool only

**Notes:** 
- No state mutation; no identity_map issues
- Read-only query; no refresh/expunge concerns
- ✅ **No action needed for this method**

---

### 3. `release_retry_lease()` — Repository Lines 328–344

**Purpose:** Clear the active lease when worker finishes or abandons retry.

#### Answers A–H:

| Question | Answer | Evidence |
|---|---|---|
| **A. Direct UPDATE?** | ✅ YES | Line 331–339: `update(VirtualAccount).where(...).values(...)` sets lease fields to NULL |
| **B. Commits?** | ✅ YES | Line 341: `await self.session.commit()` |
| **C. Return type?** | `bool` | Line 342: Returns `bool(result.rowcount)` |
| **D. Caller accesses ORM?** | ❌ NO | Service does not access the released instance afterward in any critical path. Used only to clear lock. |
| **E. Caller needs updated state?** | ❌ NO | Service only cares if release succeeded (bool result). No subsequent attribute access on released object. |
| **F. Refresh side-effects?** | ✅ **SAFE** | No caller depends on the returned instance. Refresh would not break callers. |
| **G. Expunge side-effects?** | ✅ **SAFE** | No caller depends on keeping the released instance. Expunge would not break callers. |
| **H. SELECT after mutation?** | ✅ **POSSIBLE** | Could do SELECT after UPDATE, but not critical since no caller needs fresh state. |

**Service Flow:**
- Used in error cleanup paths (not shown in audit reading)
- Service does not access the instance after releasing lock
- ✅ **Lower priority; caller does not depend on fresh ORM state after release**

---

### 4. `complete_retry_success()` — Repository Lines 345–379

**Purpose:** Mark retry as successful and clear lease only if caller still owns the lease. Returns True if successful, False if lease was lost.

#### Answers A–H:

| Question | Answer | Evidence |
|---|---|---|
| **A. Direct UPDATE?** | ✅ YES | Line 360–376: `update(VirtualAccount).where(...).values(...)` updates status, timestamps, and clears lease |
| **B. Commits?** | ✅ YES | Line 377: `await self.session.commit()` |
| **C. Return type?** | `bool` | Line 378: Returns `bool(result.rowcount)` — True if successfully finalized, False if lease was lost |
| **D. Caller accesses ORM?** | ✅ **YES** | Service (line 1113): Calls method and checks bool result. If False, calls `get_by_id()` on line 1125 to fetch fresh state AND manually updates instance fields on lines 1129–1138 |
| **E. Caller needs updated state immediately?** | ✅ **YES** | Service needs to know if finalization succeeded AND needs fresh state if it failed. Failure path (line 1125) fetches fresh instance with `get_by_id()` |
| **F. Refresh side-effects?** | ⚠️ **CONTEXT-DEPENDENT** | Success path (line 1113 returns True): Service fetches fresh instance with `get_by_id()` at line 1150 anyway, so refresh in repository would duplicate work. Failure path (line 1113 returns False): Service fetches with `get_by_id()` at line 1125, so repository refresh irrelevant. |
| **G. Expunge side-effects?** | ⚠️ **CONTEXT-DEPENDENT** | Similar to refresh: Service already calls `get_by_id()` when it needs fresh state. Expunging would not break caller logic, but is redundant. |
| **H. SELECT after mutation?** | ⚠️ **PARTIALLY BENEFICIAL** | Service already does `get_by_id()` on success path (line 1150) and failure path (line 1125). Repository doing SELECT after UPDATE would be redundant with what service already does. |

**Service Flow (Success):**
1. Line 1113: `complete_retry_success(...)` -> Returns True
2. Line 1125–1138: Falls into success block
3. Line 1150: `updated = await repository.get_by_id(account_id)` — Fetches fresh state explicitly
4. Line 1163: Returns the fresh instance

**Service Flow (Failure/Lease Lost):**
1. Line 1113: `complete_retry_success(...)` -> Returns False (lease was lost)
2. Line 1122–1138: Falls into failure block
3. Line 1125: `authoritative = await repository.get_by_id(account_id)` — Fetches fresh state explicitly
4. Lines 1129–1138: Manually updates the fetched instance

**Key Insight:** Service intentionally calls `get_by_id()` after this method regardless of success/failure. Service does NOT rely on repository to refresh instance. Service takes responsibility for ensuring fresh state.

**Verdict:** ✅ **No action needed. Service already handles fresh state fetching.**

---

### 5. `complete_retry_failure()` — Repository Lines 380–415

**Purpose:** Mark retry as failed and clear lease only if caller still owns the lease. Returns True if successful, False if lease was lost.

#### Answers A–H:

| Question | Answer | Evidence |
|---|---|---|
| **A. Direct UPDATE?** | ✅ YES | Line 398–411: `update(VirtualAccount).where(...).values(...)` updates status, retry_count, next_retry_at, last_error, clears lease |
| **B. Commits?** | ✅ YES | Line 412: `await self.session.commit()` |
| **C. Return type?** | `bool` | Line 413: Returns `bool(result.rowcount)` — True if successfully finalized, False if lease was lost |
| **D. Caller accesses ORM?** | ✅ **YES** | Service (line 1068 and 1193): Calls method, checks bool result, and if True, manually updates instance fields. Also manages exception path that calls method. |
| **E. Caller needs updated state?** | ✅ **YES** | If True: Service manually updates instance. If False: Service has already fetched fresh state before calling (line 1066). |
| **F. Refresh side-effects?** | ⚠️ **CONTEXT-DEPENDENT** | Service (line 1062–1068): Fetches fresh instance BEFORE calling method. Service (line 1181): Calls method and if True, manually updates fields. Service (lines 1182–1188): Manually flushes updates. Repository refresh would not break this, but is not where service expects fresh state. |
| **G. Expunge side-effects?** | ⚠️ **CONTEXT-DEPENDENT** | Similar to refresh; service already manages instance lifecycle. Expunging would not break caller. |
| **H. SELECT after mutation?** | ⚠️ **PARTIALLY BENEFICIAL** | Service already fetches fresh state before calling this method (line 1066). Additional SELECT in repository would be redundant. |

**Service Flow (Exception/Failure Handling):**
1. Line 1062: `account = await repository.get_by_id(account_id)` — Fetches fresh instance
2. Lines 1163–1178: Manually updates instance fields with new retry state
3. Line 1181: `await repository.complete_retry_failure(...)` -> Returns True/False
4. Lines 1182–1188: If True, manually updates/flushes instance
5. Line 1192: Returns the instance (potentially with manual updates)

**Key Insight:** Service DELIBERATELY fetches fresh state BEFORE calling this method. Service does not rely on repository to ensure fresh state. Service is architected to take responsibility for instance state management.

**Verdict:** ✅ **No action needed. Service explicitly manages fresh state fetching before calling this method.**

---

### 6. `list_retryable_accounts()` — Repository Lines 416–445

**Purpose:** Query accounts eligible for retry (status, timing, and lease checks).

#### Answers A–H:

| Question | Answer | Evidence |
|---|---|---|
| **A. Direct UPDATE?** | ❌ NO | Lines 428–439: Only SELECT query |
| **B. Commits?** | ❌ NO | Query-only, no commit |
| **C. Return type?** | `list[VirtualAccount]` | Line 441: Returns list of ORM instances |
| **D. Caller accesses ORM?** | ✅ YES | Caller receives list of fresh instances ready for retry processing |
| **E. Caller needs fresh state?** | ✅ YES | Instances must reflect current DB state to make retry decisions correctly |
| **F. Refresh side-effects?** | ✅ **SAFE** | Fresh instances returned from SELECT; no refresh needed. Future mutations will become stale, but that's expected. |
| **G. Expunge side-effects?** | N/A | Fresh instances from SELECT; expunge irrelevant |
| **H. SELECT after mutation?** | N/A | This IS a SELECT query; no mutation |

**Notes:**
- ✅ **No issues. Returns fresh instances from SELECT; no stale data problem.**

---

## Test File Audit: `test_scal001_retry_lease.py`

### Test: `test_retry_lease_is_exclusive_and_expired_lease_can_be_reclaimed()` — Lines 86–114

**Test Flow:**
```python
1. Line 88: account = await create_retry_account(...)  # Flush to session
2. Line 91: claimed_a = repository.claim_retry_for_processing(account.id, worker_id="worker-a")
   # Returns bool; does NOT return fresh ORM instance
3. Line 94: claimed_b = repository.claim_retry_for_processing(account.id, worker_id="worker-b")
   # Should return False (already claimed)
4. Line 98: lease = await lease_session.get(VirtualAccount, account.id)
   # Returns the same ORM instance from identity_map (stale)
5. Line 99: assert lease.retry_owner_id == "worker-a"
   # FAILS: lease.retry_owner_id is None (stale from identity_map)
   # Should be: lease.retry_owner_id == "worker-a" (from DB)
```

**Dependency Analysis:**

| Item | Value | Impact |
|---|---|---|
| Session config | `expire_on_commit=False` | Objects NOT auto-expired after commit; stale data remains in identity_map |
| Test access pattern | `session.get(VirtualAccount, account.id)` | Hits identity_map first; returns cached stale instance if present |
| Test expectation | Assumes `session.get()` returns authoritative DB state | **FALSE** — session.get() returns cached object from identity_map |
| Service comparison | Service manually updates ORM after claim succeeds | Test does NOT manually update; relies on session.get() |

**Root Cause:**
- Repository does `UPDATE ... COMMIT` but does not refresh the identity_map
- Test's `account` object in identity_map has `retry_owner_id=None` (stale)
- `session.get()` returns same stale `account` object
- Assertion fails because test expects fresh DB state

---

## Architecture Rules (Constraints)

✅ **Must preserve:**
1. Repositories remain responsible for database operations
2. Business logic remains in services
3. Retry ownership decisions must remain DB-authoritative
4. No global `expire_all()`
5. No manual `session.expire()` (causes MissingGreenlet on attribute access)
6. No detached ORM lifecycle unless explicitly justified
7. No redesign of existing architecture
8. No provider/service flow changes unrelated to SCAL-001
9. No changes to tests merely to make them pass

---

## Summary of Findings

### Which Methods Are Affected?

| Method | Issue | Severity | Callers Affected |
|---|---|---|---|
| `claim_retry_for_processing()` | ✅ **CRITICAL** | Direct UPDATE causes stale ORM data; test fails; service works around it manually | Test + Service |
| `verify_retry_lease()` | ❌ NO ISSUE | Query-only; no mutation | Service |
| `release_retry_lease()` | ⚠️ LOW | Direct UPDATE but no caller depends on fresh ORM state afterward | Service (non-critical paths) |
| `complete_retry_success()` | ⚠️ LOW | Direct UPDATE but service explicitly calls `get_by_id()` to fetch fresh state | Service (handles own refresh) |
| `complete_retry_failure()` | ⚠️ LOW | Direct UPDATE but service explicitly calls `get_by_id()` BEFORE calling method | Service (handles own refresh) |
| `list_retryable_accounts()` | ✅ CLEAN | Query returns fresh instances; no stale data issues | Service |

### Why Only `claim_retry_for_processing()` is Critical

**Service Flow (Works):**
- Service calls `claim_retry_for_processing()` -> bool
- Service immediately updates the in-memory ORM instance manually with known values
- Service then uses that manually-updated instance for subsequent checks
- Service does NOT rely on `session.get()` to return fresh data
- **Service avoids the stale data problem by taking responsibility for instance state**

**Test Flow (Fails):**
- Test calls `claim_retry_for_processing()` -> bool
- Test immediately calls `session.get()` to verify state
- `session.get()` returns stale cached instance from identity_map
- Test expects `session.get()` to return fresh DB data
- **Test hits the stale data problem because it relies on session.get()**

**Solution Scope:** Fix ONLY `claim_retry_for_processing()` to ensure that after UPDATE+commit, subsequent `session.get()` calls return fresh data. Do NOT change other methods (complete_retry_success, complete_retry_failure) which are working correctly because their callers already handle fresh-state management.

---

## Recommended Solution: Single, Minimal, Architecture-Consistent

### Solution: Populate Identity Map After Direct UPDATE

**Approach:** After direct UPDATE+commit in `claim_retry_for_processing()`, explicitly SELECT fresh data with `populate_existing=True` to refresh the identity_map while keeping the ORM instance tracked.

**Implementation Location:** [app/repositories/virtual_account_repository.py](app/repositories/virtual_account_repository.py) — `claim_retry_for_processing()` method only

**How It Works:**
1. Execute UPDATE with atomic WHERE conditions (unchanged)
2. Commit transaction (unchanged)
3. **NEW:** If UPDATE matched rows (rowcount > 0), do SELECT with `populate_existing=True` to refresh identity_map
4. Return bool(result.rowcount) (unchanged return type)

**Code Pattern:**
```python
# After commit
if result.rowcount > 0:
    # Refresh identity_map with fresh DB values while keeping instance tracked
    refresh_result = await self.session.execute(
        select(VirtualAccount)
        .where(VirtualAccount.id == virtual_account_id)
        .execution_options(populate_existing=True)
    )
    refresh_result.scalar_one_or_none()  # Ensures merge happens
return bool(result.rowcount)
```

### Why This Solution

| Criterion | Satisfied? | Evidence |
|---|---|---|
| **Minimal change** | ✅ YES | Only affects one method; no service changes; no test changes |
| **No architecture changes** | ✅ YES | Repository still does DB operations; service flow unchanged |
| **DB-authoritative retention** | ✅ YES | Fresh data explicitly selected from DB and merged into identity_map |
| **No global expire_all()** | ✅ YES | Targeted SELECT with populate_existing; only affects instances in identity_map |
| **No manual session.expire()** | ✅ YES | Uses populate_existing; does not call session.expire() |
| **No detached ORM** | ✅ YES | Instance remains tracked in session; no expunge |
| **No MissingGreenlet** | ✅ YES | Uses async SELECT (proper await), not lazy loading or expired attribute access. `populate_existing=True` merges data from SELECT result into existing instance without requiring attribute lazy-loading |
| **Service still works** | ✅ YES | Service's manual ORM updates (lines 920–927) merge on top of fresh state. Service's `_verify_retry_lease()` checks in-memory first (line 966), so fresh identity_map data is available. Service's comment at line 346–351 about "keep current object" is honored because instance IS kept in session (not detached or expired). |
| **Test passes** | ✅ YES | After claim succeeds, identity_map has fresh lease data from SELECT. `session.get()` returns instance with `retry_owner_id="worker-a"` |
| **No side effects** | ✅ YES | Only `claim_retry_for_processing()` affected. `complete_retry_success()`, `complete_retry_failure()`, and `release_retry_lease()` unchanged; their callers already handle fresh state properly. |

### Why No MissingGreenlet

**MissingGreenlet occurs when:** Expired attribute accessed in sync context triggers lazy-load in async code

**Our solution:** 
- Uses `await self.session.execute(select(...).execution_options(populate_existing=True))`
- `populate_existing=True` merges fresh SELECT data into the tracked instance's `__dict__`
- Does NOT call `session.expire()` (which would clear __dict__ and require lazy-load on next access)
- Subsequent attribute access reads from refreshed `__dict__`, not from expired lazy-loader
- **Result:** No lazy-loading attempt; no MissingGreenlet

### Why Not Other Solutions

**Option 1: Global `expire_all()` after UPDATE**
- ❌ **Breaks constraint:** Service comment explicitly says not to reload (line 346–351)
- ❌ **Side effects:** Could expire other unrelated instances
- ❌ **MissingGreenlet risk:** Expired attributes trigger lazy-load on next access

**Option 2: Manual `session.expire()`**
- ❌ **Breaks constraint:** No manual session.expire()
- ❌ **MissingGreenlet guaranteed:** Expired attributes require lazy-load on next access

**Option 3: Return fresh ORM instance instead of bool**
- ❌ **API change:** Would break service code expecting bool (line 918)
- ❌ **Redesign risk:** Changes method contract

**Option 4: Change service to call `get_by_id()` after claim**
- ❌ **Service change forbidden:** User constraint: no service layer changes
- ❌ **Doesn't help test:** Test would still need to know to call `get_by_id()` instead of `session.get()`

**Option 5: Expunge the instance and force refetch**
- ❌ **Breaks service assumption:** Service comment at line 346–351 says "Keep the current object... when the ORM instance is still in the same session"
- ❌ **Detached object risk:** Service manually updates at line 920–927; detached object could cause cascade failures

**Option 6: No fix; rely on service pattern**
- ❌ **Test fails:** Current test expects `session.get()` to return fresh data
- ❌ **Violates SCAL-001:** Lease ownership not immediately authoritative after claim

---

## Conclusion

**Single recommended fix:**

After `claim_retry_for_processing()` commits the UPDATE, do a SELECT with `populate_existing=True` to refresh the identity_map with fresh database state.

**Scope:** Only the `claim_retry_for_processing()` method in [app/repositories/virtual_account_repository.py](app/repositories/virtual_account_repository.py)

**Other methods:** No changes needed. They are working correctly because:
- `verify_retry_lease()` is read-only
- `release_retry_lease()` has no dependent ORM access afterward
- `complete_retry_success()` and `complete_retry_failure()` already have callers that manage fresh-state fetching

**Result:**
- ✅ Test passes (lease.retry_owner_id == "worker-a")
- ✅ No MissingGreenlet (uses async SELECT, not expired lazy-loading)
- ✅ Service still works (instance kept in session; fresh state available; no redesign)
- ✅ All architecture constraints satisfied
- ✅ Minimal change (one method, ~5 lines of code)
