"""
Module 5 — Provider GitLab (API REST v4 réelle)

Crée des issues GitLab et interroge leur statut via l'API REST v4 documentée :
https://docs.gitlab.com/ee/api/issues.html

Authentification : header `PRIVATE-TOKEN` (Personal/Project Access Token) —
https://docs.gitlab.com/ee/api/rest/authentication.html

⚠️ Prototype : conçu pour un projet GitLab sandbox (settings.GITLAB_PROJECT_ID).
Ne jamais pointer vers un projet de production.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.integrations.base import BaseTicketProvider, TicketNotFoundError, TicketProviderError
from app.schemas.ticket import CreatedTicket, TicketDraft

logger = logging.getLogger(__name__)

# GitLab n'a pas de "issue type" natif comme Jira : on encode le type sous
# forme de label, pattern standard GitLab (ex: "type::bug").
_ISSUE_TYPE_LABEL: dict[str, str] = {
    "story": "type::story",
    "bug": "type::bug",
    "task": "type::task",
}


class GitLabProvider(BaseTicketProvider):
    """Provider GitLab — création et suivi d'issues via l'API REST v4."""

    def __init__(
        self,
        base_url: str | None = None,
        private_token: str | None = None,
        project_id: str | None = None,
        timeout: int = 30,
    ):
        self.base_url = (base_url or settings.GITLAB_BASE_URL or "").rstrip("/")
        self.private_token = private_token or settings.GITLAB_PRIVATE_TOKEN
        self.project_id = project_id or settings.GITLAB_PROJECT_ID
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "gitlab"

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.private_token or ""}

    async def create_ticket(self, draft: TicketDraft, session_id: str) -> CreatedTicket:
        if not (self.base_url and self.private_token and self.project_id):
            raise TicketProviderError(
                "Configuration GitLab incomplète (GITLAB_BASE_URL / GITLAB_PRIVATE_TOKEN / GITLAB_PROJECT_ID)",
                provider=self.provider_name,
            )

        labels = list(draft.labels) + [_ISSUE_TYPE_LABEL.get(draft.issue_type, "type::task")]
        payload = {
            "title": draft.title,
            "description": draft.description,
            "labels": ",".join(labels),
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/v4/projects/{self.project_id}/issues",
                    data=payload,
                    headers=self._headers(),
                )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise TicketProviderError(
                f"GitLab n'a pas répondu dans le délai imparti ({self.timeout}s)",
                provider=self.provider_name,
            ) from exc
        except httpx.ConnectError as exc:
            raise TicketProviderError(
                f"Impossible de se connecter à GitLab ({self.base_url})", provider=self.provider_name
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise TicketProviderError(
                f"GitLab a renvoyé HTTP {exc.response.status_code}: {exc.response.text}",
                provider=self.provider_name,
                details={"status_code": exc.response.status_code},
            ) from exc

        now = datetime.now(timezone.utc)
        logger.info("GitLab issue created: #%s", data["iid"])
        return CreatedTicket(
            provider=self.provider_name,
            external_id=str(data["iid"]),
            key=f"#{data['iid']}",
            url=data["web_url"],
            title=draft.title,
            status=data.get("state", "opened"),
            session_id=session_id,
            user_story_title=draft.title,
            created_at=now,
            last_synced_at=now,
        )

    async def get_ticket_status(self, external_id: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/v4/projects/{self.project_id}/issues/{external_id}",
                    headers=self._headers(),
                )
            if response.status_code == 404:
                raise TicketNotFoundError(
                    f"Issue GitLab '#{external_id}' introuvable", provider=self.provider_name
                )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise TicketProviderError(
                f"GitLab n'a pas répondu dans le délai imparti ({self.timeout}s)",
                provider=self.provider_name,
            ) from exc
        except httpx.ConnectError as exc:
            raise TicketProviderError(
                f"Impossible de se connecter à GitLab ({self.base_url})", provider=self.provider_name
            ) from exc
        except httpx.HTTPStatusError as exc:
            # Contrat BaseTicketProvider : tout échec non-404 doit sortir en
            # TicketProviderError (sinon un 401/500 GitLab traverse
            # refresh_ticket_statuses et casse le suivi, cf. module 6).
            raise TicketProviderError(
                f"GitLab a renvoyé HTTP {exc.response.status_code}: {exc.response.text}",
                provider=self.provider_name,
                details={"status_code": exc.response.status_code},
            ) from exc

        return data["state"]

    async def health_check(self) -> bool:
        if not (self.base_url and self.private_token and self.project_id):
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/api/v4/projects/{self.project_id}", headers=self._headers()
                )
            return response.status_code == 200
        except httpx.HTTPError:
            # Un health check ne lève jamais : toute erreur httpx (connexion,
            # timeout, transport) signifie simplement "indisponible".
            return False


__all__ = ["GitLabProvider"]
