# SCAL-001 SQLAlchemy Behavior Audit Report

**Executive Summary**

The test failure is NOT due to `MissingGreenlet`. The actual failure is **stale cached object data in the session identity_map**. After a direct SQL UPDATE with `synchronize_session=False`, the loaded VirtualAccount instance retains old attribute values in memory. When `AsyncSession.get()` is called later, it returns this stale cached object from the identity_map instead of fetching fresh data from the database.

---

## Diagnostic Findings

### A. Does `synchronize_session=False` itself mark the instance as expired?

**Evidence:** ❌ **NO**

```
BEFORE UPDATE:
  expired: False
AFTER UPDATE, BEFORE COMMIT:
  expired: False
AFTER COMMIT (expire_on_commit=False):
  expired: False
```

With `synchronize_session=False`, the loaded VirtualAccount instance remains:
- `account._sa_instance_state.expired = False`
- `account.__dict__` contains all previously loaded attributes
- The object is actively tracked, not marked for reload

### B. Does `synchronize_session="fetch"` mark the instance as expired?

**Evidence:** ❌ **NO** (identical to False and None)

```
synchronize_session="fetch" behavior:
BEFORE UPDATE:
  expired: False
AFTER COMMIT:
  expired: False
  __dict__ still contains: status="PENDING", all old values intact
```

Even with `"fetch"`, the loaded instance is NOT expired and retains stale data. **This suggests synchronize_session only affects ORM-loaded instances, not instances already in memory, or "fetch" re-fetches but doesn't refresh loaded instances properly.**

### C. Does `commit()` expire it despite `expire_on_commit=False`?

**Evidence:** ❌ **NO**

```
AFTER COMMIT (expire_on_commit=False):
  expired: False
  __dict__ keys: unchanged
  'id' in __dict__: True (still present)
  'retry_owner_id' in __dict__: False (never was, because it wasn't loaded initially)
```

The session configuration `expire_on_commit=False` is respected. Objects are never auto-expired by commit.

### D. Does any current repository helper explicitly call `session.expire()`, `expire_all()`, `refresh()`, or otherwise invalidate the instance?

**Evidence:** ❌ **NO**

Current repository code in `claim_retry_for_processing()`:
```python
result = await self.session.execute(
    update(VirtualAccount)
    .where(...)
    .values(...)
    .execution_options(synchronize_session=False)
)
await self.session.commit()
return bool(result.rowcount)
```

No explicit expiration, refresh, or cleanup. The object remains in the identity_map unchanged.

### E. Which exact operation changes `account._sa_instance_state.expired` from False to True?

**Evidence:** **Only manual `session.expire(account)` call**

```
Before session.expire(account):
  expired: False

After session.expire(account):
  expired: True
  __dict__ cleared to only contain: ['_sa_instance_state']
```

Operations that do NOT trigger expiration:
- ❌ Direct UPDATE statement
- ❌ Commit (with expire_on_commit=False)
- ❌ synchronize_session options
- ✅ Only: Explicit `session.expire(account)` call

### F. At what exact line does `MissingGreenlet` occur?

**Evidence:** **NOT during UPDATE/commit. Only when accessing expired attributes in synchronous context.**

When `session.expire()` is called and the object is marked expired:
```
After expire():
  expired: True
Trying to access account.id after expire():
  FAILED: MissingGreenlet: greenlet_spawn has not been called; 
  can't call await_only() here.
```

**The MissingGreenlet occurs when accessing ANY attribute on an expired object in a sync descriptor context.** This is because SQLAlchemy's descriptor tries to lazy-load the expired attribute, which requires async IO, but `__get__` is synchronous.

### G. Is `account.id` itself expired, or only lease-related attributes?

**Evidence:** **Neither. Attributes are not "expired" individually. Instead, the entire instance state is expired (or not).**

When an instance is NOT expired (normal case after UPDATE+commit):
```
'id' in __dict__: True          ← Attribute data present
'retry_owner_id' in __dict__: False  ← Never loaded, not in __dict__
```

When an instance IS expired (after session.expire()):
```
__dict__ cleared to: ['_sa_instance_state']  ← ALL attributes removed
'id' in __dict__: False        ← Gone
'retry_owner_id' in __dict__: False  ← Also gone
```

**Expiration is all-or-nothing at the instance level, not per-attribute.**

### H. Does `AsyncSession.get()` return the existing identity-map instance rather than querying the database?

**Evidence:** ✅ **YES**

```
retrieved = await session.get(VirtualAccount, account_id)
same object? True              ← Same Python object identity
```

And the retrieved object is stale:
```
'retry_owner_id' in __dict__: False  ← Not loaded, outdated in DB
```

**Verification:** When we `session.expunge(account)` to remove it from identity_map:
```
[session.expunge(account)]
  Object removed from identity_map

[session.get() after expunge]
  same object? False            ← NEW object fetched from DB
  retrieved.retry_owner_id: worker-b  ← Fresh data from DB
```

---

## Root Cause

**The test failure on line 98 (`assert lease.retry_owner_id == "worker-a"`):**

1. Test creates `account` object and flushes to session → enters identity_map with fresh attributes
2. Test calls `repository.claim_retry_for_processing(account.id, worker_id="worker-a")` → repository does direct UPDATE in database, commits
3. Repository's commit does NOT refresh/expire the `account` object in the test's session
4. Test later calls `await lease_session.get(VirtualAccount, account.id)` → session finds the stale `account` object in identity_map, returns it
5. Test accesses `lease.retry_owner_id` → value is `None` (stale), not `"worker-a"` (actual DB value)
6. Assertion fails: `assert None == "worker-a"`

**Why the original conversation had MissingGreenlet errors:**

Earlier repository code attempts (in previous conversation segments) may have included calls to `session.expire()` or other expiration logic that marked the object as expired. When the test later tried to access the expired object's attributes in a sync context (descriptor `__get__`), SQLAlchemy's lazy-loader attempted async IO from sync code, causing MissingGreenlet.

---

## Solutions Validated by Diagnostics

### Solution 1: `await session.refresh(account)` ✅

**Works.** After direct UPDATE+commit, calling `await session.refresh()` explicitly reloads object attributes from the database.

```python
await self.session.execute(update_stmt)
await self.session.commit()
# Refresh object to ensure it's in sync with DB
refreshed_obj = await self.session.get(VirtualAccount, virtual_account_id)
if refreshed_obj:
    await self.session.refresh(refreshed_obj)
```

**Result:**
```
After refresh():
  expired: False
  retry_owner_id: worker-a  ← Loaded from DB
```

### Solution 2: `session.expunge()` to force refetch ✅

**Works.** Removing the object from identity_map forces `session.get()` to fetch fresh from DB.

```python
await self.session.execute(update_stmt)
await self.session.commit()
# Remove object from identity_map to force refetch
obj = await self.session.get(VirtualAccount, virtual_account_id)
if obj:
    self.session.expunge(obj)
# Next session.get() will fetch fresh
```

**Result:**
```
After expunge + session.get():
  same object? False  ← New object fetched from DB
  retry_owner_id: worker-b  ← Fresh data
```

### Solution 3: `synchronize_session="fetch"` ❌

**Does NOT work.** Despite documentation, `"fetch"` on direct UPDATE statements does not refresh already-loaded instances.

```
With synchronize_session="fetch":
AFTER COMMIT:
  expired: False
  __dict__ still contains old values
```

---

## What Happens Without Any Fix

```
Repository behavior:
  await session.execute(update(...).execution_options(synchronize_session=False))
  await session.commit()
  return bool(result.rowcount)  ← Returns correct row count

Session identity_map after:
  account._sa_instance_state.expired = False
  account.retry_owner_id = None  ← STALE (DB has "worker-a")

Test's session.get(account_id):
  Returns the stale account from identity_map
  lease.retry_owner_id = None  ← Wrong value
  Assertion fails
```

---

## Pylance Diagnostic Issue

**Lines 311, 348, 383, 420:** `Cannot access attribute "rowcount" for class "Result[Any]"`

**Cause:** The SQLAlchemy type stub file (sqlalchemy-stubs) does not include `rowcount` in the type definition for `Result[Any]`, even though the attribute exists at runtime.

**Status:** Type-checking issue only. At runtime, `.rowcount` works correctly on `CursorResult` objects returned by `execute()` with UPDATE statements.

**Not a blocker for the actual bug fix.**

---

## Conclusion

The SCAL-001 test failure is fundamentally about **stale object data caching in AsyncSession's identity_map**. The MissingGreenlet errors from earlier attempts were likely caused by trying to access expired attributes in sync contexts.

**Recommended Fix Category:** After each direct UPDATE+commit that modifies tracked fields, either refresh the affected objects or remove them from the identity_map to ensure subsequent accesses get fresh data from the database.

