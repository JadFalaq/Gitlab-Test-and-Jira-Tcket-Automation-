"""
Module 3 — Test Design Agent : Schémas d'interface (Input/Output)

Transforme un RequirementAnalysis (sortie du module 2) en artefacts de
test structurés : user stories, features Gherkin, résumé de couverture.

Design decisions:
- Le LLM ne génère jamais de texte Gherkin brut : il produit un JSON
  structuré (GherkinFeature/GherkinScenario/GherkinStep) que le code
  rend ensuite en syntaxe Gherkin standard via `render_gherkin_feature`
  (voir app/agents/test_design_agent.py). Ça évite au LLM d'inventer
  une variante de syntaxe et garde le rendu déterministe et testable.
- `GherkinStep.keyword` est contraint aux 5 mots-clés Gherkin standard
  (Given/When/Then/And/But) — cf. règle anti-hallucination du projet
  qui interdit un format Gherkin custom.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.requirement import RequirementAnalysis

_GHERKIN_TAG_RE = re.compile(r"^@\S+$")


def _validate_gherkin_tags(tags: list[str]) -> list[str]:
    """
    Valide que chaque tag suit la syntaxe Gherkin standard (préfixe '@',
    aucun espace/saut de ligne interne, ex: '@smoke').

    Sans ce contrôle, un LLM déstabilisé par un contexte volumineux (ex:
    texte extrait d'une pièce jointe PDF, cf. module 2) peut placer du texte
    libre dans "tags" au lieu de vrais tags — ce texte devient alors la
    première ligne du fichier .feature rendu (cf. gherkin_renderer.py) et
    casse le parseur Cucumber avec une erreur peu exploitable. Rejeter ici,
    au niveau Pydantic, déclenche le même mécanisme de retry correctif que
    le reste du module (cf. test_design_agent.py) avec un message ciblé.
    """
    cleaned = [tag.strip() for tag in tags]
    for tag in cleaned:
        if not _GHERKIN_TAG_RE.match(tag):
            raise ValueError(
                f"tag invalide {tag!r} : un tag Gherkin doit commencer par '@' et "
                "ne contenir aucun espace (ex: '@smoke', '@feature-paiement')"
            )
    return cleaned


# ---------------------------------------------------------------------------
# Input (JSON structuré du module 2)
# ---------------------------------------------------------------------------

# Le module 3 consomme directement un RequirementAnalysis (module 2),
# pas de schéma d'enveloppe supplémentaire nécessaire.


# ---------------------------------------------------------------------------
# Gherkin (sous-schémas)
# ---------------------------------------------------------------------------

class GherkinStep(BaseModel):
    """Une étape d'un scénario Gherkin."""

    keyword: Literal["Given", "When", "Then", "And", "But"] = Field(
        ...,
        description="Mot-clé Gherkin standard (pas de variante custom)",
    )
    text: str = Field(..., min_length=1, description="Texte de l'étape")


class GherkinScenario(BaseModel):
    """Un scénario Gherkin (Scenario: ...)."""

    name: str = Field(..., min_length=1, description="Nom du scénario")
    steps: list[GherkinStep] = Field(
        ...,
        min_length=1,
        description="Étapes du scénario, dans l'ordre",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags du scénario (ex: '@smoke'), avec le préfixe '@'",
    )

    @field_validator("tags")
    @classmethod
    def _tags_are_valid_gherkin(cls, value: list[str]) -> list[str]:
        return _validate_gherkin_tags(value)


class GherkinFeature(BaseModel):
    """Une feature Gherkin (Feature: ...), rendue ensuite en fichier .feature."""

    feature_name: str = Field(..., min_length=1, description="Nom de la feature")
    description: str = Field(default="", description="Description de la feature")
    scenarios: list[GherkinScenario] = Field(
        ...,
        min_length=1,
        description="Scénarios de la feature",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags de la feature (ex: '@feature-qr-payment')",
    )

    @field_validator("tags")
    @classmethod
    def _tags_are_valid_gherkin(cls, value: list[str]) -> list[str]:
        return _validate_gherkin_tags(value)


# ---------------------------------------------------------------------------
# User Story
# ---------------------------------------------------------------------------

class UserStory(BaseModel):
    """Une user story au format 'As a / I want / So that'."""

    title: str = Field(..., min_length=1, description="Titre court de la user story")
    as_a: str = Field(..., min_length=1, description="Persona ('As a ...')")
    i_want: str = Field(..., min_length=1, description="Besoin ('I want ...')")
    so_that: str = Field(..., min_length=1, description="Bénéfice ('So that ...')")
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Critères d'acceptation de la user story",
    )
    priority: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Priorité de la user story",
    )


# ---------------------------------------------------------------------------
# Résumé de couverture
# ---------------------------------------------------------------------------

class TestSummary(BaseModel):
    """Résumé de la couverture de test générée."""

    total_scenarios: int = Field(..., ge=0, description="Nombre total de scénarios générés")
    coverage_notes: str = Field(
        default="",
        description="Notes sur ce qui est couvert / manquant (ex: tests négatifs à ajouter)",
    )
    suggested_test_types: list[str] = Field(
        default_factory=list,
        description="Types de tests suggérés (ex: functional, smoke, negative)",
    )


# ---------------------------------------------------------------------------
# Output complet du module 3
# ---------------------------------------------------------------------------

class TestDesignOutput(BaseModel):
    """
    Sortie complète du Test Design Agent.

    Sortie du module 3, entrée du module 4 (interface de validation).
    """

    user_stories: list[UserStory] = Field(..., min_length=1)
    gherkin_features: list[GherkinFeature] = Field(..., min_length=1)
    test_summary: TestSummary


__all__ = [
    "RequirementAnalysis",  # ré-exporté : input direct du module 3
    "GherkinStep",
    "GherkinScenario",
    "GherkinFeature",
    "UserStory",
    "TestSummary",
    "TestDesignOutput",
]
