"""
Tests unitaires — Rendu et validation Gherkin (utilisé par le module 3)

Vérifie que :
- Le rendu produit une syntaxe Gherkin standard (Feature/Scenario/Given-When-Then)
- Le texte rendu est syntaxiquement valide selon le parseur officiel Cucumber
- Un step malformé (ex: texte contenant un saut de ligne) est bien rejeté

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest

from app.agents.gherkin_renderer import (
    GherkinSyntaxError,
    render_and_validate,
    render_gherkin_feature,
    validate_gherkin_syntax,
)
from app.schemas.test_design import GherkinFeature, GherkinScenario, GherkinStep


def _fictional_feature() -> GherkinFeature:
    return GherkinFeature(
        feature_name="[FICTIF] Paiement par QR Code",
        description="En tant qu'utilisateur, je veux payer via QR code",
        tags=["@feature-qr-payment"],
        scenarios=[
            GherkinScenario(
                name="Paiement réussi via scan QR",
                tags=["@smoke", "@payment"],
                steps=[
                    GherkinStep(keyword="Given", text="l'utilisateur est connecté"),
                    GherkinStep(keyword="And", text="un QR code de paiement valide est affiché"),
                    GherkinStep(keyword="When", text="l'utilisateur scanne le QR code"),
                    GherkinStep(keyword="Then", text="le montant est affiché"),
                    GherkinStep(keyword="And", text="un reçu de paiement est généré"),
                ],
            )
        ],
    )


class TestRenderGherkinFeature:
    def test_render_contains_feature_and_scenario_keywords(self):
        text = render_gherkin_feature(_fictional_feature())
        assert "Feature: [FICTIF] Paiement par QR Code" in text
        assert "Scenario: Paiement réussi via scan QR" in text
        assert "@feature-qr-payment" in text
        assert "@smoke @payment" in text

    def test_render_contains_all_steps_in_order(self):
        text = render_gherkin_feature(_fictional_feature())
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        step_lines = [l for l in lines if l.split(" ", 1)[0] in ("Given", "When", "Then", "And", "But")]
        assert step_lines == [
            "Given l'utilisateur est connecté",
            "And un QR code de paiement valide est affiché",
            "When l'utilisateur scanne le QR code",
            "Then le montant est affiché",
            "And un reçu de paiement est généré",
        ]

    def test_render_without_tags_or_description(self):
        feature = GherkinFeature(
            feature_name="Minimal",
            scenarios=[
                GherkinScenario(
                    name="s1",
                    steps=[GherkinStep(keyword="Given", text="x"), GherkinStep(keyword="Then", text="y")],
                )
            ],
        )
        text = render_gherkin_feature(feature)
        assert "Feature: Minimal" in text
        assert "Scenario: s1" in text


class TestValidateGherkinSyntax:
    def test_valid_gherkin_passes(self):
        text = render_gherkin_feature(_fictional_feature())
        validate_gherkin_syntax(text)  # ne doit pas lever

    def test_invalid_gherkin_raises(self):
        with pytest.raises(GherkinSyntaxError):
            validate_gherkin_syntax("ceci n'est pas du gherkin du tout\nligne 2\n")

    def test_step_with_embedded_newline_is_rejected(self):
        """Un step avec un saut de ligne interne casse la syntaxe rendue."""
        feature = GherkinFeature(
            feature_name="Cassé",
            scenarios=[
                GherkinScenario(
                    name="s1",
                    steps=[
                        GherkinStep(keyword="Given", text="quelque chose\nde cassé"),
                        GherkinStep(keyword="Then", text="ok"),
                    ],
                )
            ],
        )
        with pytest.raises(GherkinSyntaxError):
            render_and_validate(feature)


class TestRenderAndValidate:
    def test_render_and_validate_returns_text_for_valid_feature(self):
        text = render_and_validate(_fictional_feature())
        assert "Feature:" in text
        assert "Scenario:" in text
