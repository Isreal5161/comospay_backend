"""Phase 1: Database Data Validation for provider_reference uniqueness constraint"""
import sqlite3
import sys
import os

def validate_database():
    """Check if database has conflicts with the planned UNIQUE(provider_name, provider_reference) constraint"""
    
    # Try SQLite first (development)
    db_path = 'cosmozpay.db'
    if not os.path.exists(db_path):
        print("\n" + "=" * 80)
        print("PHASE 1: DATABASE DATA VALIDATION")
        print("=" * 80)
        print("\nℹ️ INFO: Database file not found at", db_path)
        print("   This is expected for development environment with migrations not yet run")
        print("\n" + "=" * 80)
        return True
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print("\n" + "=" * 80)
        print("PHASE 1: DATABASE DATA VALIDATION")
        print("=" * 80)
        
        # Check if transactions table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'")
        if not cursor.fetchone():
            print("\nℹ️ INFO: transactions table does not exist (clean database)")
            conn.close()
            return True
        
        print("\n1. TRANSACTIONS TABLE SIZE:")
        cursor.execute("SELECT COUNT(*) FROM transactions")
        count = cursor.fetchone()[0]
        print(f"   Total records: {count}")
        
        if count == 0:
            print("\n✓ Database is empty, no duplicate check needed")
            conn.close()
            return True
        
        print("\n2. NULL ANALYSIS:")
        cursor.execute("SELECT COUNT(*) FROM transactions WHERE provider_reference IS NULL")
        null_count = cursor.fetchone()[0]
        print(f"   NULL provider_reference: {null_count}")
        
        cursor.execute("SELECT COUNT(*) FROM transactions WHERE provider_name IS NULL")
        null_pname = cursor.fetchone()[0]
        print(f"   NULL provider_name: {null_pname}")
        
        print("\n3. GLOBAL provider_reference DUPLICATES:")
        cursor.execute("""
            SELECT provider_reference, COUNT(*) as cnt 
            FROM transactions 
            WHERE provider_reference IS NOT NULL 
            GROUP BY provider_reference 
            HAVING COUNT(*) > 1
        """)
        global_dupes = cursor.fetchall()
        if global_dupes:
            print(f"   ⚠️  Found {len(global_dupes)} duplicate provider_reference values:")
            for pref, cnt in global_dupes[:10]:
                print(f"       {pref}: {cnt} records")
            conn.close()
            return False
        else:
            print("   ✓ No global duplicates found")
        
        print("\n4. PROVIDER-SCOPED DUPLICATES (provider_name, provider_reference):")
        cursor.execute("""
            SELECT provider_name, provider_reference, COUNT(*) as cnt 
            FROM transactions 
            WHERE provider_reference IS NOT NULL 
            GROUP BY provider_name, provider_reference 
            HAVING COUNT(*) > 1
        """)
        provider_dupes = cursor.fetchall()
        if provider_dupes:
            print(f"   ⚠️  Found {len(provider_dupes)} duplicate pairs:")
            for pname, pref, cnt in provider_dupes[:10]:
                print(f"       ({pname}, {pref}): {cnt} records")
            conn.close()
            return False
        else:
            print("   ✓ No provider-scoped duplicates found")
        
        print("\n5. PROVIDER DISTRIBUTION:")
        cursor.execute("""
            SELECT provider_name, COUNT(*) as cnt 
            FROM transactions 
            WHERE provider_name IS NOT NULL 
            GROUP BY provider_name 
            ORDER BY cnt DESC
        """)
        providers = cursor.fetchall()
        for pname, cnt in providers:
            print(f"   {pname}: {cnt} records")
        
        print("\n" + "=" * 80)
        print("RESULT: Database validation PASSED - no conflicts found")
        print("=" * 80)
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"\n❌ Error validating database: {e}")
        return False

if __name__ == "__main__":
    success = validate_database()
    sys.exit(0 if success else 1)
