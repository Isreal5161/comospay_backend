import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import AsyncSessionFactory
from app.models.provider import Provider


async def main() -> None:
    async with AsyncSessionFactory() as session:
        existing = await session.execute(select(Provider).where(Provider.code == 'smtp'))
        provider = existing.scalar_one_or_none()
        if provider is None:
            provider = Provider(
                code='smtp',
                name='smtp',
                category='Email',
                description='Default SMTP email provider for MAIL_PROVIDER=smtp.',
                status='active',
                is_active=True,
                environment='production',
                priority=0,
                metadata_payload='{"health_score": 100.0, "success_rate": 100.0, "failure_count": 0, "maintenance_mode": false}',
                health_status='healthy',
            )
            session.add(provider)
            await session.commit()
            await session.refresh(provider)
            print('created', provider.code, provider.name, provider.category, provider.status, provider.is_active, provider.environment)
        else:
            print('existing', provider.code, provider.name, provider.category, provider.status, provider.is_active, provider.environment)


if __name__ == '__main__':
    asyncio.run(main())
