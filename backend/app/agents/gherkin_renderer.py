"""
Rendu et validation Gherkin — utilisé par le module 3 (Test Design Agent)

Le LLM ne produit jamais de texte Gherkin brut (cf. schemas/test_design.py) :
il produit du JSON structuré, que ce module rend en syntaxe Gherkin standard
(Feature/Scenario/Given/When/Then) puis valide avec le parseur officiel
Cucumber (`gherkin-official`), garantissant une syntaxe conforme sans jamais
inventer de variante custom (règle anti-hallucination du projet).
"""

from __future__ import annotations

from gherkin.errors import CompositeParserException
from gherkin.parser import Parser
from gherkin.token_scanner import TokenScanner

from app.schemas.test_design import GherkinFeature


class GherkinSyntaxError(Exception):
    """Le texte Gherkin rendu n'est pas syntaxiquement valide."""

    def __init__(self, message: str, feature_text: str):
        self.feature_text = feature_text
        super().__init__(message)


def render_gherkin_feature(feature: GherkinFeature) -> str:
    """Rend un GherkinFeature en texte .feature standard (Feature/Scenario/Given-When-Then)."""

    lines: list[str] = []

    if feature.tags:
        lines.append(" ".join(feature.tags))
    lines.append(f"Feature: {feature.feature_name}")

    if feature.description:
        for desc_line in feature.description.splitlines():
            lines.append(f"  {desc_line}")

    for scenario in feature.scenarios:
        lines.append("")
        if scenario.tags:
            lines.append("  " + " ".join(scenario.tags))
        lines.append(f"  Scenario: {scenario.name}")
        for step in scenario.steps:
            lines.append(f"    {step.keyword} {step.text}")

    return "\n".join(lines) + "\n"


def validate_gherkin_syntax(feature_text: str) -> None:
    """
    Valide qu'un texte .feature est syntaxiquement correct via le parseur
    officiel Cucumber.

    Raises:
        GherkinSyntaxError: si le texte n'est pas un Gherkin valide.
    """
    try:
        Parser().parse(TokenScanner(feature_text))
    except CompositeParserException as exc:
        raise GherkinSyntaxError(str(exc), feature_text=feature_text) from exc


def render_and_validate(feature: GherkinFeature) -> str:
    """Rend un GherkinFeature puis valide sa syntaxe. Lève GherkinSyntaxError si invalide."""
    text = render_gherkin_feature(feature)
    validate_gherkin_syntax(text)
    return text
