"""
Module 4 — Interface de validation : modèle de persistance

Une `SessionModel` représente une demande d'ingénieur qui traverse le
pipeline module 2 (analyse) -> module 3 (conception de tests) -> validation
humaine (accept/reject/regenerate). Voir app/services/session_service.py
pour la machine à états (`status`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserModel(Base):
    """
    Module 8 — Multi-utilisateur (22/07/2026) : un compte par assigné,
    authentifié via SSO externe (OIDC générique — Google Workspace ou
    Microsoft Entra ID, cf. app/auth/oidc.py). Aucun mot de passe
    stocké ici : l'authentification est entièrement déléguée au fournisseur
    SSO, ce modèle ne fait que refléter l'identité renvoyée par son endpoint
    /userinfo.

    Créé/mis à jour (upsert par `email`) à chaque login réussi — voir
    app/services/auth_service.py::upsert_user. Tous les comptes utilisent le
    même compte de service Jira (settings.JIRA_*, cf. module 5) : ce modèle
    ne stocke aucun identifiant Jira individuel.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Identifiant "sub" renvoyé par le fournisseur OIDC — stable même si
    # l'email affiché change côté annuaire d'entreprise. Sert de repère
    # secondaire ; l'email reste la clé d'upsert principale (cf.
    # auth_service.upsert_user).
    oidc_subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)

    # Module 8 — propriétaire de la session (cf. UserModel ci-dessus) : une
    # session n'est visible/actionnable que par l'assigné qui l'a créée
    # (filtrage explicite dans session_service.py, même convention "pas de
    # relationship SQLAlchemy" que session_id sur AttachmentModel/GenerationModel).
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # Machine à états : voir SessionStatus dans app/schemas/session.py
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Module 9 (22/07/2026) — phase de clarification conversationnelle avant
    # l'analyse structurée (statut 'chatting', cf. SessionStatus). Liste de
    # dicts {role: "user"|"assistant", content: str, created_at: str (ISO)},
    # jamais vide après create_session (le premier message utilisateur y est
    # dupliqué). Le transcript complet est injecté dans le contexte envoyé au
    # module 2 lors de finalize_chat — voir session_service.run_requirement_analysis.
    chat_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Stockés en JSON brut (dict) — validés contre RequirementAnalysis /
    # TestDesignOutput au passage par les schémas Pydantic (app/schemas/session.py)
    requirement_analysis: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    test_design_output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Module 5 — Intégration Jira : tickets créés pour cette session
    # (liste de dicts sérialisés depuis CreatedTicket, voir app/schemas/ticket.py)
    # nullable=False + default=list : toujours une liste (jamais None), pour
    # que SessionResponse.tickets (list[CreatedTicket]) se construise sans
    # coercion particulière depuis from_attributes.
    tickets: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Module 7 — Analytics : compteurs incrémentés par session_service.py à
    # chaque regenerate_requirement/regenerate_tests. Stockés ici plutôt que
    # recalculés (l'historique des générations elles-mêmes vit dans
    # GenerationModel ci-dessous, pas dans ces compteurs).
    requirement_regenerations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    test_regenerations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    # Module 7 — Analytics : figé une seule fois par accept_tests() (passage
    # à 'validated'), ne bouge plus ensuite — contrairement à updated_at qui
    # continue de changer à chaque sync de tickets (module 6). Corrige
    # average_pipeline_duration_seconds, qui gonflait indéfiniment avant
    # (cf. docs/module_7_analytics.md, point d'incertitude #1).
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class GenerationModel(Base):
    """
    Module 4 (extension) — historique des générations LLM (modules 2/3) par
    session, avec la décision humaine associée.

    Chaque exécution de run_requirement_analysis/run_test_design (initiale
    ou via regenerate_*) crée une ligne 'pending'. Elle devient :
    - 'accepted' via accept_requirement/accept_tests ;
    - 'rejected' via reject_requirement/reject_tests (explicite), ou
      implicitement par regenerate_requirement/regenerate_tests (l'ingénieur
      choisit de relancer plutôt que d'accepter — la génération encore
      'pending' à ce moment est marquée 'rejected' avant de relancer) ;
    - 'failed' directement si l'appel LLM a échoué (pas de décision humaine
      possible, `content` reste NULL, `error_message` renseigné).

    Objectif : disposer de paires (rejected, accepted) exploitables pour du
    few-shot RAG ou un futur fine-tuning par préférence (DPO) — cf. échange
    avec l'utilisateur sur RAG/DLP/RL (19-21/07/2026).

    `embedding` (ajouté 21/07/2026) : vecteur d'embedding de la demande
    initiale masquée (DLP), calculé pour les générations stage='requirement'
    uniquement (cf. app/services/rag_service.py) — utilisé pour le RAG
    few-shot : retrouver les générations 'accepted' passées sémantiquement
    proches d'une nouvelle demande, à injecter comme exemples dans le prompt
    du module 2. NULL pour stage='test_design' (pas encore de RAG sur ce
    stage) et pour toute génération dont le calcul d'embedding a échoué
    (best-effort, ne bloque jamais la génération elle-même).
    """

    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # "requirement" (module 2) | "test_design" (module 3)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)

    # 0 = génération initiale, N = Nième régénération — aligné sur
    # SessionModel.requirement_regenerations / test_regenerations au moment
    # de l'appel.
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # Snapshot JSON de RequirementAnalysis ou TestDesignOutput ; NULL si
    # l'appel LLM a échoué (voir error_message dans ce cas).
    content: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # "pending" | "accepted" | "rejected" | "failed" — voir docstring de classe.
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # RAG (module 2, extension) — voir docstring de classe ci-dessus.
    # none_as_null=True : par défaut, le type JSON de SQLAlchemy stocke un
    # Python None comme le littéral JSON "null" (valeur SQL non-NULL), pas
    # comme un vrai NULL — ce qui casserait le filtre
    # `GenerationModel.embedding.is_not(None)` de rag_service.py (une ligne
    # sans embedding remonterait quand même). none_as_null=True force le
    # vrai NULL SQL pour que ce filtre fonctionne.
    embedding: Mapped[Optional[list]] = mapped_column(JSON(none_as_null=True), nullable=True)


class AttachmentModel(Base):
    """
    Module 2 (extension) — pièces jointes fournies par l'ingénieur à la
    création d'une session (photos, PDF, Word, Excel, PowerPoint, texte),
    pour enrichir la compréhension de sa demande.

    Cycle de vie :
    - Créée au kickoff synchrone (create_session) : fichier écrit sur disque
      (app/services/attachment_service.py), `extracted_text`/`extraction_error`
      encore NULL.
    - Traitée dans la tâche de fond run_requirement_analysis, AVANT l'appel
      LLM : extraction de texte (document) ou description factuelle
      (image, via le modèle vision — app/agents/vision_layer.py). Le résultat
      est concaténé au `context` de la session avant de construire le prompt
      du module 2 — même principe d'entrée unique que le DLP (cf.
      dlp_masking.py) : ce texte, une fois construit, passe par le même
      masquage que le reste de `context`.
    - Best-effort strict : une extraction/description qui échoue (fichier
      corrompu, type non reconnu, Ollama/modèle vision indisponible) est
      consignée dans `extraction_error`, ne bloque jamais l'analyse.

    Le fichier lui-même vit sur disque (`settings.UPLOAD_DIR`), jamais en
    base — seul le chemin relatif est stocké ici. `filename` (nom original,
    potentiellement contrôlé par l'utilisateur) n'est JAMAIS utilisé pour
    construire un chemin disque (risque de traversée de répertoire) : le nom
    de fichier réel sur disque est dérivé de `id` (UUID), pas de `filename`.
    """

    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)

    # "image" (décrite par le modèle vision) | "document" (texte extrait)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    # Chemin relatif sous settings.UPLOAD_DIR (jamais dérivé de `filename`).
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)

    # Texte extrait (document) ou description générée (image) — injecté dans
    # le contexte envoyé au module 2. NULL tant que non traité, ou si
    # l'extraction/description a échoué (voir extraction_error).
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extraction_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
