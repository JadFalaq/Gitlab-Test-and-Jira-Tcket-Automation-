r"""
Migration ponctuelle (passage à Jira uniquement) : nettoie les tickets
stockés dans `sessions.tickets` qui ne correspondent à aucun vrai ticket Jira.

Supprime :
- les tickets GitLab (provider "gitlab"), GitLab n'étant plus supporté ;
- les tickets de l'ancien provider factice (provider "mock", ou URL
  https://example-mock.invalid/...), qui n'ont jamais existé dans Jira.

Une session 'tickets_created' qui n'a plus aucun ticket repasse en
'validated' : l'ingénieur peut alors créer ses vrais tickets Jira.

Idempotent. Affiche ce qui serait fait sans --apply.

Usage :
    cd backend
    ..\..\venv\Scripts\activate
    python scripts/migrate_jira_only.py            # aperçu
    python scripts/migrate_jira_only.py --apply    # applique
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.session import SessionModel  # noqa: E402

_MOCK_URL_PREFIX = "https://example-mock.invalid/"


def is_real_jira_ticket(ticket: dict) -> bool:
    return ticket.get("provider") == "jira" and not str(ticket.get("url", "")).startswith(_MOCK_URL_PREFIX)


def migrate_session(session: SessionModel) -> tuple[int, bool]:
    """Nettoie une session en place. Retourne (tickets retirés, statut changé)."""
    tickets = list(session.tickets or [])
    kept = [t for t in tickets if is_real_jira_ticket(t)]
    removed = len(tickets) - len(kept)
    status_changed = False
    if removed:
        session.tickets = kept
        if not kept and session.status == "tickets_created":
            session.status = "validated"
            session.error_message = None
            status_changed = True
    return removed, status_changed


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as db:
        sessions = (await db.execute(select(SessionModel))).scalars().all()
        total_removed = 0
        for session in sessions:
            removed, status_changed = migrate_session(session)
            if removed:
                total_removed += removed
                note = " -> statut remis à 'validated'" if status_changed else ""
                print(f"session {session.id}: {removed} ticket(s) retiré(s){note}")

        if not total_removed:
            print("Rien à migrer.")
            return
        if apply:
            await db.commit()
            print(f"Appliqué : {total_removed} ticket(s) retiré(s).")
        else:
            await db.rollback()
            print(f"Aperçu seulement : {total_removed} ticket(s) seraient retiré(s). Relancer avec --apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="appliquer la migration (sinon aperçu)")
    asyncio.run(main(parser.parse_args().apply))
