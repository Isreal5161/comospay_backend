import sys
import os
import pytest

# Ensure project root is importable when tests run in isolated environments
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.provider.selector import ProviderSelector


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, name, value, nx=False, ex=None):
        if nx:
            if name in self.store:
                return False
            self.store[name] = value
            return True
        self.store[name] = value
        return True

    async def get(self, name):
        return self.store.get(name)

    async def delete(self, name):
        if name in self.store:
            del self.store[name]
            return 1
        return 0

    async def scan_iter(self, match=None):
        # simple implementation: yield matching keys
        import fnmatch

        for key in list(self.store.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


@pytest.mark.asyncio
async def test_invalidate_provider_cache_removes_matching_keys():
    fake = FakeRedis()

    # prepare keys
    keys = [
        "provider:selection:airtime",
        "provider:selection:airtime:vodafone",
        "provider:selection:airtime:vodafone:prod",
        "provider:selection:payments",
    ]
    for k in keys:
        await fake.set(k, "x")

    selector = ProviderSelector(provider_repository=object(), redis_client=fake)
    await selector.invalidate_provider_cache(category="airtime")

    # ensure airtime keys removed and payments key remains
    assert "provider:selection:airtime" not in fake.store
    assert "provider:selection:airtime:vodafone" not in fake.store
    assert "provider:selection:airtime:vodafone:prod" not in fake.store
    assert "provider:selection:payments" in fake.store
