from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.core.config import settings

# Seconds asyncpg waits to open a connection (its default is 60).
DB_CONNECT_TIMEOUT_SECONDS = 10

engine = create_async_engine(
    settings.database_url.get_secret_value(),
    echo=False,
    # Neon (and most hosted Postgres) closes idle connections; check before use and
    # recycle well inside the server's idle timeout instead of failing a request.
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"timeout": DB_CONNECT_TIMEOUT_SECONDS},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Matches PostgreSQL's own default names (users_pkey, users_email_key, ...), so the
# existing schema and new migrations agree, and every constraint has a stable name.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


class Base(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
