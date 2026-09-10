"""
Module 5 — Provider Jira Cloud (API REST v3 réelle)

Crée des issues Jira et interroge leur statut via l'API REST v3 documentée :
https://developer.atlassian.com/cloud/jira/platform/rest/v3/

Authentification : Basic Auth (email + API token), comme recommandé par
Atlassian pour Jira Cloud — https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/

⚠️ Prototype : conçu pour un projet Jira sandbox/bac à sable (cf.
settings.JIRA_PROJECT_KEY par défaut = "PROTO"). Ne jamais pointer vers un
projet de production.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.integrations.base import BaseTicketProvider, TicketNotFoundError, TicketProviderError
from app.schemas.ticket import CreatedTicket, TicketDraft

logger = logging.getLogger(__name__)

# Jira Cloud attend des noms de type d'issue standard du schéma par défaut.
_ISSUE_TYPE_MAP: dict[str, str] = {
    "story": "Story",
    "bug": "Bug",
    "task": "Task",
}


def _text_to_adf(text: str) -> dict:
    """
    Convertit un texte brut en Atlassian Document Format (ADF) minimal —
    requis par le champ `description` de l'API Jira v3 (texte brut refusé).
    Un paragraphe ADF par ligne non vide du texte source.
    """
    paragraphs = [p for p in text.split("\n\n") if p.strip()] or [text or ""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs
        ],
    }


class JiraProvider(BaseTicketProvider):
    """Provider Jira Cloud — création et suivi d'issues via l'API REST v3."""

    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
        user_email: str | None = None,
        project_key: str | None = None,
        timeout: int = 30,
    ):
        self.base_url = (base_url or settings.JIRA_BASE_URL or "").rstrip("/")
        self.api_token = api_token or settings.JIRA_API_TOKEN
        self.user_email = user_email or settings.JIRA_USER_EMAIL
        self.project_key = project_key or settings.JIRA_PROJECT_KEY
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "jira"

    def _auth_header(self) -> dict[str, str]:
        token = f"{self.user_email}:{self.api_token}".encode("utf-8")
        return {"Authorization": f"Basic {base64.b64encode(token).decode('ascii')}"}

    async def create_ticket(self, draft: TicketDraft, session_id: str) -> CreatedTicket:
        if not (self.base_url and self.api_token and self.user_email):
            raise TicketProviderError(
                "Configuration Jira incomplète (JIRA_BASE_URL / JIRA_API_TOKEN / JIRA_USER_EMAIL)",
                provider=self.provider_name,
            )

        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": draft.title,
                "issuetype": {"name": _ISSUE_TYPE_MAP.get(draft.issue_type, "Task")},
                "description": _text_to_adf(draft.description),
                "labels": draft.labels,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/rest/api/3/issue",
                    json=payload,
                    headers=self._auth_header(),
                )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise TicketProviderError(
                f"Jira n'a pas répondu dans le délai imparti ({self.timeout}s)",
                provider=self.provider_name,
            ) from exc
        except httpx.ConnectError as exc:
            raise TicketProviderError(
                f"Impossible de se connecter à Jira ({self.base_url})", provider=self.provider_name
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise TicketProviderError(
                f"Jira a renvoyé HTTP {exc.response.status_code}: {exc.response.text}",
                provider=self.provider_name,
                details={"status_code": exc.response.status_code},
            ) from exc

        key = data["key"]
        now = datetime.now(timezone.utc)
        logger.info("Jira issue created: %s", key)
        await self._try_add_to_active_sprint(key)
        return CreatedTicket(
            provider=self.provider_name,
            external_id=data["id"],
            key=key,
            url=f"{self.base_url}/browse/{key}",
            title=draft.title,
            status="To Do",  # statut initial standard d'un workflow Jira par défaut
            session_id=session_id,
            user_story_title=draft.title,
            created_at=now,
            last_synced_at=now,
        )

    async def _try_add_to_active_sprint(self, issue_key: str) -> None:
        """
        Best-effort : rattache l'issue au sprint actif du board Scrum du
        projet, si un tel board/sprint existe.

        Pourquoi : un ticket créé via l'API Jira REST v3 atterrit dans le
        backlog, jamais automatiquement sur le tableau Scrum actif — un
        tableau Scrum n'affiche que les issues du sprint en cours. Sans ce
        rattachement, un ticket "créé" reste invisible pour l'ingénieur tant
        qu'il ne va pas le chercher manuellement dans le backlog.

        Ne lève jamais et n'empêche jamais la création du ticket elle-même
        (déjà actée à ce stade) : un projet Kanban (pas de sprint), un board
        sans sprint actif, ou une erreur réseau ponctuelle se traduisent
        juste par un ticket qui reste dans le backlog — dégradation
        silencieuse assumée, comme le reste du module 5 (best-effort).
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                boards_resp = await client.get(
                    f"{self.base_url}/rest/agile/1.0/board",
                    params={"projectKeyOrId": self.project_key},
                    headers=self._auth_header(),
                )
                if boards_resp.status_code != 200:
                    return
                for board in boards_resp.json().get("values", []):
                    sprints_resp = await client.get(
                        f"{self.base_url}/rest/agile/1.0/board/{board['id']}/sprint",
                        params={"state": "active"},
                        headers=self._auth_header(),
                    )
                    if sprints_resp.status_code != 200:
                        # Board Kanban (pas de concept de sprint) ou autre
                        # limite de l'API agile pour ce board : on ignore et
                        # on essaie le board suivant.
                        continue
                    active_sprints = sprints_resp.json().get("values", [])
                    if not active_sprints:
                        continue
                    sprint_id = active_sprints[0]["id"]
                    move_resp = await client.post(
                        f"{self.base_url}/rest/agile/1.0/sprint/{sprint_id}/issue",
                        json={"issues": [issue_key]},
                        headers=self._auth_header(),
                    )
                    if move_resp.status_code < 300:
                        logger.info(
                            "Jira: %s rattachée au sprint actif '%s' (id=%s)",
                            issue_key,
                            active_sprints[0].get("name"),
                            sprint_id,
                        )
                    return
        except httpx.HTTPError:
            logger.info("Jira: impossible de rattacher %s à un sprint actif (ignoré)", issue_key)

    async def get_ticket_status(self, external_id: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/rest/api/3/issue/{external_id}",
                    params={"fields": "status"},
                    headers=self._auth_header(),
                )
            if response.status_code == 404:
                raise TicketNotFoundError(
                    f"Issue Jira '{external_id}' introuvable", provider=self.provider_name
                )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise TicketProviderError(
                f"Jira n'a pas répondu dans le délai imparti ({self.timeout}s)",
                provider=self.provider_name,
            ) from exc
        except httpx.ConnectError as exc:
            raise TicketProviderError(
                f"Impossible de se connecter à Jira ({self.base_url})", provider=self.provider_name
            ) from exc
        except httpx.HTTPStatusError as exc:
            # Contrat BaseTicketProvider : tout échec non-404 doit sortir en
            # TicketProviderError (sinon un 401/500 Jira traverse
            # refresh_ticket_statuses et casse le suivi, cf. module 6).
            raise TicketProviderError(
                f"Jira a renvoyé HTTP {exc.response.status_code}: {exc.response.text}",
                provider=self.provider_name,
                details={"status_code": exc.response.status_code},
            ) from exc

        return data["fields"]["status"]["name"]

    async def health_check(self) -> bool:
        if not (self.base_url and self.api_token and self.user_email):
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/rest/api/3/myself", headers=self._auth_header())
            return response.status_code == 200
        except httpx.HTTPError:
            # Un health check ne lève jamais : toute erreur httpx (connexion,
            # timeout, transport) signifie simplement "indisponible".
            return False


__all__ = ["JiraProvider"]
