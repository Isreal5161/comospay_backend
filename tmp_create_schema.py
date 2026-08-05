import sys
sys.path.insert(0, 'c:/Users/HP/Downloads/CosmozPay/CosmozPay-Backend')
import asyncio
from app.database.base import Base
import app.models
from app.config.database import engine

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('create_all ok')
    await engine.dispose()

asyncio.run(main())
