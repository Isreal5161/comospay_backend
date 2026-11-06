import sys
sys.path.insert(0, 'c:/Users/HP/Downloads/CosmozPay/CosmozPay-Backend')
import app.models
from app.database.base import Base
import collections

counts = collections.Counter()
for tbl in Base.metadata.tables.values():
    for idx in tbl.indexes:
        counts[idx.name] += 1

for name, count in counts.items():
    if count > 1:
        print(name, count)
