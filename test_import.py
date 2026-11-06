#!/usr/bin/env python
"""Test app import and startup."""
try:
    from app.main import app
    print("✓ App imported successfully")
    print(f"✓ App has {len(app.routes)} routes")
except Exception as e:
    print(f"✗ ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
