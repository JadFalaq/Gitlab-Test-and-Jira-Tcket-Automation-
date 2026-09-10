"""
Tests — Module 2 (extension) : DLP (app/agents/dlp_masking.py)

NoOpMaskingProvider est trivial. PresidioMaskingProvider est testé avec le
vrai moteur (Presidio + spaCy fr_core_news_lg) — plus lent que le reste de
la suite (chargement du modèle au premier test, quelques secondes), mais
c'est le seul moyen de vérifier que le masquage détecte réellement des
entités françaises plutôt que de faire confiance à une intégration jamais
exécutée. get_masking_provider() (la factory utilisée en production) est
testée séparément via settings.DLP_MASK_ENABLED.

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest

from app.agents.dlp_masking import (
    NoOpMaskingProvider,
    PresidioMaskingProvider,
    get_masking_provider,
)
from app.config import settings


class TestNoOpMaskingProvider:
    def test_returns_text_unchanged(self):
        provider = NoOpMaskingProvider()

        assert provider.mask("Marie Dupont, marie@fictif.test") == "Marie Dupont, marie@fictif.test"

    def test_handles_empty_string(self):
        assert NoOpMaskingProvider().mask("") == ""


class TestPresidioMaskingProvider:
    """Moteur réel — premier test de la classe plus lent (chargement spaCy)."""

    @pytest.fixture(scope="class")
    @classmethod
    def provider(cls):
        return PresidioMaskingProvider()

    def test_masks_email_address(self, provider):
        # Domaine avec un vrai TLD (.fr) : EmailRecognizer valide le TLD via
        # tldextract, et un TLD réservé RFC 2606 (.test/.example/.invalid)
        # est légitimement rejeté comme "pas un email réel" — pas un bug,
        # mais une source de faux négatif à connaître pour choisir des
        # données fictives réalistes ailleurs (ex: tickets Jira de démo).
        result = provider.mask("Merci de me recontacter à marie.dupont@exemple-fictif.fr pour ce bug.")

        assert "marie.dupont@exemple-fictif.fr" not in result
        assert "<EMAIL_ADDRESS>" in result

    def test_masks_french_phone_number(self, provider):
        result = provider.mask("Mon numéro est le 06 12 34 56 78 si besoin.")

        assert "06 12 34 56 78" not in result

    def test_masks_person_name_in_free_text(self, provider):
        """Le cas que la regex ne peut pas couvrir : un nom propre en texte
        libre, sans format structuré reconnaissable."""
        result = provider.mask(
            "D'après le retour de Marie Dupont chez [FICTIF] Client X SA, le bug apparaît au paiement."
        )

        assert "Marie Dupont" not in result
        assert "<PERSON>" in result
        # Le reste de la phrase (contexte métier, non sensible) doit survivre
        assert "bug" in result
        assert "paiement" in result

    def test_leaves_text_without_pii_unchanged(self, provider):
        text = "En tant qu'utilisateur, je veux exporter mes tâches terminées en PDF."

        assert provider.mask(text) == text

    def test_handles_empty_string(self, provider):
        assert provider.mask("") == ""


class TestGetMaskingProvider:
    def test_returns_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "DLP_MASK_ENABLED", False)

        assert isinstance(get_masking_provider(), NoOpMaskingProvider)

    def test_returns_presidio_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "DLP_MASK_ENABLED", True)

        assert isinstance(get_masking_provider(), PresidioMaskingProvider)
