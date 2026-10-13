import asyncio
import getpass
import os
import sys

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.database import AsyncSessionLocal, engine  # noqa: E402
from src.core.security import hash_password  # noqa: E402
from src.models.user_model import User  # noqa: E402


async def seed_admin() -> None:
    name = os.getenv("ADMIN_NAME", "").strip()
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "")

    if not email and sys.stdin.isatty():
        email = input("Admin email: ").strip().lower()
    if not name and sys.stdin.isatty():
        name = input("Admin name [Portfolio Administrator]: ").strip()
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Admin password: ")

    name = name or "Portfolio Administrator"

    if not email or not password:
        raise RuntimeError(
            "Set ADMIN_EMAIL and ADMIN_PASSWORD, or run make seed-admin in an interactive terminal."
        )
    if len(password) < 12:
        raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters long.")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        admin = result.scalar_one_or_none()

        if admin is None:
            admin = User(
                name=name,
                email=email,
                hashed_password=hash_password(password),
                is_active=True,
                is_superuser=True,
            )
            session.add(admin)
            action = "created"
        else:
            admin.name = name
            admin.hashed_password = hash_password(password)
            admin.is_active = True
            admin.is_superuser = True
            action = "updated"

        await session.commit()
        print(f"Admin account {action}: {email}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_admin())
