"""
Module 4 — Interface de validation : Schémas d'interface (Input/Output)

Une session traverse : chatting -> pending_requirement_review -> pending_test_review
-> validated -> tickets_created, avec des sorties de secours (rejected / failed) à
chaque étape. Voir app/services/session_service.py pour la machine à états complète.

Design decisions:
- Pas d'édition ciblée par champ dans cette version (cf. ordre de dev du
  projet) : "regenerate" relance entièrement le module concerné (2 ou 3).
  Depuis le module 8 (22/07/2026), un `guidance` texte libre optionnel peut
  accompagner un regenerate (cf. RegenerateRequest ci-dessous) : ce n'est
  toujours pas une édition ciblée par champ, juste une instruction en
  langage naturel injectée dans le prompt de CET appel, jamais stockée.
- "reject" est terminal — pour réessayer, l'ingénieur crée une nouvelle
  session plutôt que de faire revivre une session rejetée.
- "tickets_created" (module 5) est atteint depuis "validated" ou
  "ticket_creation_failed" (retry après un échec Jira).
- Module 9 (22/07/2026) : "chatting" est le TOUT premier statut d'une session
  (avant "analyzing") — une phase de clarification conversationnelle libre
  avec le LLM (cf. app/agents/chat_agent.py), pas de génération structurée
  tant que l'ingénieur n'a pas explicitement cliqué "Générer l'analyse"
  (POST .../chat/finalize). Objectif : ne jamais présenter un résultat
  structuré + bouton de validation en une seule interaction confiante — cf.
  retour utilisateur du 22/07/2026 ("le modèle est strict et confiant,
  laisse le droit à l'utilisateur de discuter").
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field

from app.agents.gherkin_renderer import render_gherkin_feature

from app.schemas.attachment import AttachmentResponse
from app.schemas.requirement import RequirementAnalysis
from app.schemas.test_design import TestDesignOutput
from app.schemas.ticket import CreatedTicket

SessionStatus = Literal[
    "chatting",  # module 9 — clarification conversationnelle en cours, voir docstring de tête
    "analyzing",  # module 2 en cours (tâche de fond) — voir session_service.py
    "pending_requirement_review",
    "requirement_failed",
    "requirement_rejected",
    "designing_tests",  # module 3 en cours (tâche de fond) — voir session_service.py
    "pending_test_review",
    "test_design_failed",
    "tests_rejected",
    "validated",
    "ticket_creation_failed",
    "tickets_created",
]


class SessionCreateRequest(BaseModel):
    """Entrée : le tout premier message de l'ingénieur, qui ouvre la phase
    de clarification conversationnelle (statut 'chatting', module 9)."""

    user_input: str = Field(..., min_length=1, description="Texte libre décrivant le besoin")
    context: Optional[str] = Field(default=None, description="Contexte additionnel optionnel")


class ChatMessage(BaseModel):
    """Un message de la conversation de clarification (module 9)."""

    role: Literal["user", "assistant"]
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSendRequest(BaseModel):
    """Corps de POST .../chat : un nouveau message de l'ingénieur dans la
    conversation de clarification."""

    message: str = Field(..., min_length=1, description="Message de l'ingénieur")


class RegenerateRequest(BaseModel):
    """
    Corps optionnel de POST .../regenerate (module 8) : une instruction
    libre pour CETTE régénération uniquement (ex: "concentre-toi sur les cas
    d'erreur"), injectée dans le prompt LLM de cet appel, jamais persistée
    sur la session (cf. app/services/session_service.py).
    """

    guidance: Optional[str] = Field(default=None, description="Instruction libre pour cette régénération")


class SessionResponse(BaseModel):
    """Détail complet d'une session, à tout moment de son cycle de vie."""

    id: str
    status: SessionStatus
    raw_input: str
    context: Optional[str] = None
    chat_history: list[ChatMessage] = Field(
        default_factory=list,
        description="Conversation de clarification (module 9) — toujours au moins 1 message après création",
    )
    requirement_analysis: Optional[RequirementAnalysis] = None
    test_design_output: Optional[TestDesignOutput] = None
    error_message: Optional[str] = Field(
        default=None,
        description=(
            "Détail de l'erreur si status='requirement_failed', 'test_design_failed' "
            "ou 'ticket_creation_failed'"
        ),
    )
    tickets: list[CreatedTicket] = Field(
        default_factory=list, description="Tickets créés (module 5), vide tant que non créés"
    )
    requirement_regenerations: int = 0
    test_regenerations: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = Field(
        default=None,
        description="Figé au passage à 'validated' (module 7 — durée du pipeline), ne bouge plus ensuite",
    )
    attachments: list[AttachmentResponse] = Field(
        default_factory=list,
        description="Photos/documents joints à la création (module 2, extension) — traités en tâche de fond",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gherkin_feature_files(self) -> list[str]:
        """Texte .feature de chaque feature de test_design_output, dans le même
        ordre. Rendu par le backend (même fonction que pour les tickets Jira
        et la validation Cucumber) : le frontend l'affiche tel quel."""
        if self.test_design_output is None:
            return []
        return [render_gherkin_feature(feature) for feature in self.test_design_output.gherkin_features]

    model_config = {"from_attributes": True}


class SessionSummary(BaseModel):
    """Version allégée pour les vues de liste (GET /api/sessions)."""

    id: str
    status: SessionStatus
    title: str = Field(..., description="Titre de l'analyse si disponible, sinon extrait de raw_input")
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
