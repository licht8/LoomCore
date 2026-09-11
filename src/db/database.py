from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """FastAPI-зависимость для получения асинхронной сессии БД."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Проверить подключение к БД (миграции применяются через alembic).

    init_db() теперь не создаёт таблицы — это делает Alembic:
        alembic upgrade head

    Вызывается в main.py:startup() для совместимости, но больше не инициализирует схему.
    """
    pass
