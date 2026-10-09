"""
Module 2 — Requirement Analyzer Agent : Schémas d'interface (Input/Output)

Ces schémas normalisent l'analyse d'une demande brute d'ingénieur
(texte libre / chat) en un JSON structuré exploitable par le module 3
(Test Design Agent).

Design decisions:
- Les noms de champs reprennent exactement l'exemple fictif défini dans
  FakeLLMProvider.DEFAULT_RESPONSES["analyze_requirement"] (tests/fakes/llm.py),
  pour que cet exemple reste valide sans duplication de schéma.
- `ambiguities` n'est jamais bloquant : l'agent liste les zones floues,
  c'est le module 4 (validation humaine) qui tranche.
- `confidence_score` reflète la confiance du LLM dans son analyse, pas
  une vérité absolue — sert de signal pour prioriser la relecture humaine.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Input (texte libre / chat depuis l'ingénieur)
# ---------------------------------------------------------------------------

class RequirementAnalysisRequest(BaseModel):
    """Entrée du Requirement Analyzer Agent : la demande brute de l'ingénieur."""

    user_input: str = Field(
        ...,
        min_length=1,
        description="Texte libre décrivant le besoin, bug ou idée de feature",
    )
    context: Optional[str] = Field(
        default=None,
        description=(
            "Contexte additionnel optionnel (échanges précédents du chat, "
            "précisions apportées par l'ingénieur après une première analyse)"
        ),
    )


# ---------------------------------------------------------------------------
# Output (JSON structuré consommé par le module 3)
# ---------------------------------------------------------------------------

class RequirementAnalysis(BaseModel):
    """
    Analyse structurée d'une demande d'ingénieur.

    Sortie du module 2, entrée du module 3 (Test Design Agent).
    """

    title: str = Field(..., min_length=1, description="Titre court et clair de la demande")
    type: Literal["feature", "bug", "improvement", "other"] = Field(
        ...,
        description="Nature de la demande",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="Description reformulée et clarifiée du besoin",
    )
    user_personas: list[str] = Field(
        default_factory=list,
        description="Profils utilisateurs concernés par ce besoin",
    )
    functional_criteria: list[str] = Field(
        default_factory=list,
        description="Critères fonctionnels explicites ou déduits de la demande",
    )
    non_functional_criteria: list[str] = Field(
        default_factory=list,
        description="Contraintes non-fonctionnelles (perf, sécurité, etc.)",
    )
    priority: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Priorité estimée de la demande",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Dépendances techniques ou fonctionnelles identifiées",
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description=(
            "Points flous ou questions ouvertes que l'agent n'a pas pu "
            "résoudre à partir du texte fourni. Non-bloquant : à trancher "
            "par l'ingénieur dans l'interface de validation (module 4)."
        ),
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confiance du LLM dans la qualité de son analyse (0-1)",
    )
