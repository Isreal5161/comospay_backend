import sqlite3

conn = sqlite3.connect('cosmozpay.db')
cursor = conn.cursor()

print("=" * 80)
print("DATABASE INSPECTION: provider_reference ANALYSIS")
print("=" * 80)

print("\n1. TRANSACTIONS TABLE:")
print("-" * 80)

cursor.execute("SELECT COUNT(*) FROM transactions")
total = cursor.fetchone()[0]
print(f"Total transaction records: {total}")

cursor.execute("SELECT COUNT(*) FROM transactions WHERE provider_reference IS NULL")
null_count = cursor.fetchone()[0]
print(f"Transactions with NULL provider_reference: {null_count}")

# Duplicates (GLOBAL)
cursor.execute("""
    SELECT provider_reference, COUNT(*) as cnt 
    FROM transactions 
    WHERE provider_reference IS NOT NULL 
    GROUP BY provider_reference 
    HAVING COUNT(*) > 1
    LIMIT 10
""")
duplicates = cursor.fetchall()
print(f"Duplicate provider_reference values (GLOBAL): {len(duplicates)}")
if duplicates:
    for pref, cnt in duplicates:
        print(f"  {pref}: {cnt} records")

# Duplicates by provider
cursor.execute("""
    SELECT provider_name, provider_reference, COUNT(*) as cnt 
    FROM transactions 
    WHERE provider_reference IS NOT NULL 
    GROUP BY provider_name, provider_reference 
    HAVING COUNT(*) > 1
    LIMIT 10
""")
provider_dupes = cursor.fetchall()
print(f"\nDuplicate (provider_name, provider_reference) pairs: {len(provider_dupes)}")
if provider_dupes:
    for prov, pref, cnt in provider_dupes:
        print(f"  ({prov}, {pref}): {cnt} records")

print("\n2. VIRTUAL_ACCOUNTS TABLE:")
print("-" * 80)

cursor.execute("SELECT COUNT(*) FROM virtual_accounts")
va_total = cursor.fetchone()[0]
print(f"Total virtual account records: {va_total}")

cursor.execute("SELECT COUNT(*) FROM virtual_accounts WHERE provider_reference IS NULL")
va_null = cursor.fetchone()[0]
print(f"Virtual accounts with NULL provider_reference: {va_null}")

cursor.execute("""
    SELECT provider_reference, COUNT(*) as cnt 
    FROM virtual_accounts 
    WHERE provider_reference IS NOT NULL 
    GROUP BY provider_reference 
    HAVING COUNT(*) > 1
    LIMIT 10
""")
va_dupes = cursor.fetchall()
print(f"Duplicate provider_reference values: {len(va_dupes)}")
if va_dupes:
    for pref, cnt in va_dupes:
        print(f"  {pref}: {cnt} records")

print("\n3. BANK_ACCOUNTS TABLE:")
print("-" * 80)

cursor.execute("SELECT COUNT(*) FROM bank_accounts")
ba_total = cursor.fetchone()[0]
print(f"Total bank account records: {ba_total}")

cursor.execute("SELECT COUNT(*) FROM bank_accounts WHERE provider_reference IS NULL")
ba_null = cursor.fetchone()[0]
print(f"Bank accounts with NULL provider_reference: {ba_null}")

cursor.execute("""
    SELECT provider_reference, COUNT(*) as cnt 
    FROM bank_accounts 
    WHERE provider_reference IS NOT NULL 
    GROUP BY provider_reference 
    HAVING COUNT(*) > 1
    LIMIT 10
""")
ba_dupes = cursor.fetchall()
print(f"Duplicate provider_reference values (GLOBAL): {len(ba_dupes)}")
if ba_dupes:
    for pref, cnt in ba_dupes:
        print(f"  {pref}: {cnt} records")

cursor.execute("""
    SELECT provider_name, provider_reference, COUNT(*) as cnt 
    FROM bank_accounts 
    WHERE provider_reference IS NOT NULL 
    GROUP BY provider_name, provider_reference 
    HAVING COUNT(*) > 1
    LIMIT 10
""")
ba_provider_dupes = cursor.fetchall()
print(f"Duplicate (provider_name, provider_reference) pairs: {len(ba_provider_dupes)}")
if ba_provider_dupes:
    for prov, pref, cnt in ba_provider_dupes:
        print(f"  ({prov}, {pref}): {cnt} records")

print("\n" + "=" * 80)

conn.close()
