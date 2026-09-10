"""
Connexion base de données — engine SQLAlchemy async + factory de sessions.

Utilisé par le module 4 (persistance des sessions de validation). SQLite
en phase prototype (voir settings.DATABASE_URL), remplaçable par n'importe
quelle base supportée par SQLAlchemy async sans changer le reste du code.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.session import Base

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dépendance FastAPI : fournit une session DB par requête."""
    async with AsyncSessionLocal() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Dépendance FastAPI : fournit la factory de sessions elle-même (pas une
    session déjà ouverte). Utilisée par les tâches de fond (FastAPI
    BackgroundTasks, cf. session_service.run_*_task) qui doivent ouvrir leur
    propre AsyncSession après la fin de la requête HTTP — celle de get_db()
    est fermée à ce moment-là. Overridable en test comme get_db, pour que
    les tâches de fond écrivent dans la DB de test plutôt que dans le
    fichier configuré par DATABASE_URL.
    """
    return AsyncSessionLocal


async def init_db() -> None:
    """Crée les tables si elles n'existent pas encore (pas de migration en prototype)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
