"""
Module 2 (extension) — DLP : masquage des données personnelles avant tout
envoi externe (LLM, puis transitivement Jira via le module 5).

Vit dans app/agents/ (comme llm_layer.py) et pas dans app/services/ : c'est
une infrastructure LLM-adjacente utilisée par RequirementAnalyzerAgent, pas
une orchestration métier — respecte le sens de dépendance services -> agents
déjà établi (session_service importe requirement_analyzer, jamais l'inverse).

Point d'interception unique : RequirementAnalyzerAgent.analyze() (module 2)
— c'est le SEUL endroit où le texte brut de l'ingénieur (session.raw_input /
session.context) entre dans le pipeline. Le module 3 (Test Design) ne
travaille JAMAIS sur raw_input directement, seulement sur le
RequirementAnalysis produit par le module 2 (cf.
session_service._run_test_design) : si le module 2 masque avant l'appel LLM,
tout ce qui en dérive — module 3, puis les tickets Jira créés par le
module 5 (ticket_service._build_drafts) — hérite du masquage sans logique
dupliquée à chaque point de sortie.

session.raw_input N'EST PAS masqué en base : c'est la saisie de l'ingénieur
sur sa propre machine (SQLite local), aucune fuite externe associée à ce
stockage. Seul ce qui PART réellement (LLM, puis tickets) est masqué.

Architecture : même pattern que llm_layer.py (BaseLLMProvider) et
app/integrations/base.py (BaseTicketProvider) — interface abstraite +
providers interchangeables + factory pilotée par settings, cohérent avec la
convention "Providers interchangeables" du projet.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base Provider (ABC)
# ---------------------------------------------------------------------------

class BaseMaskingProvider(ABC):
    """Interface abstraite pour tout provider de masquage DLP."""

    @abstractmethod
    def mask(self, text: str) -> str:
        """
        Retourne `text` avec les entités sensibles remplacées par des
        placeholders (ex: "Marie Dupont" -> "<PERSON>", format par défaut de
        l'AnonymizerEngine — pas de numérotation par occurrence : deux noms
        différents dans le même texte deviennent tous deux "<PERSON>"). Sans
        effet sur du texte sans entité détectée (retourne le texte
        inchangé). Ne lève jamais : un échec de masquage ne doit pas
        empêcher l'analyse (comportement propre à chaque implémentation).
        """
        ...


# ---------------------------------------------------------------------------
# NoOp Provider (DLP désactivé / tests)
# ---------------------------------------------------------------------------

class NoOpMaskingProvider(BaseMaskingProvider):
    """
    Ne masque rien — utilisé quand DLP_MASK_ENABLED=false, et forcé dans
    toute la suite de tests (cf. tests/conftest.py::_disable_dlp_masking_in_tests)
    pour que les assertions basées sur des mots-clés (FakeLLMProvider des tests) restent
    déterministes, et pour ne pas payer le chargement du modèle spaCy
    (~1-2s) à chaque run.
    """

    def mask(self, text: str) -> str:
        return text


# ---------------------------------------------------------------------------
# Presidio Provider (masquage réel, NER local via spaCy)
# ---------------------------------------------------------------------------

# Entités couvertes : PERSON/LOCATION/ORGANIZATION nécessitent le modèle
# spaCy français (NER contextuel) ; EMAIL_ADDRESS/PHONE_NUMBER/IBAN_CODE sont
# des reconnaisseurs Presidio génériques (regex + validation). CREDIT_CARD
# est volontairement exclu : son reconnaisseur par défaut n'est pas
# enregistré pour le français dans le registre Presidio (warning au
# chargement sinon, aucune détection possible de toute façon).
# Note : EMAIL_ADDRESS valide le TLD du domaine via tldextract — un domaine
# en .test/.example/.invalid (RFC 2606, réservés à la documentation) est
# légitimement rejeté comme "pas un email réel" (cf. tests/test_dlp_masking.py).
_ENTITIES = [
    "PERSON",
    "LOCATION",
    "ORGANIZATION",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IBAN_CODE",
]

_LANGUAGE = "fr"
_SPACY_MODEL = "fr_core_news_lg"

_engines_lock = threading.Lock()
_engines: Optional[tuple] = None


def _get_presidio_engines():
    """
    Charge AnalyzerEngine/AnonymizerEngine une seule fois par process
    (singleton paresseux, thread-safe). presidio_analyzer/spacy ne sont
    importés que si le DLP réel est effectivement utilisé — jamais pendant
    la suite de tests (DLP_MASK_ENABLED forcé à False, cf. conftest.py),
    donc pas de coût de chargement du modèle spaCy pour `pytest`.
    """
    global _engines
    if _engines is None:
        with _engines_lock:
            if _engines is None:
                from presidio_analyzer import AnalyzerEngine
                from presidio_analyzer.nlp_engine import NlpEngineProvider
                from presidio_anonymizer import AnonymizerEngine

                nlp_configuration = {
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": _LANGUAGE, "model_name": _SPACY_MODEL}],
                }
                nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()
                analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[_LANGUAGE])
                anonymizer = AnonymizerEngine()
                _engines = (analyzer, anonymizer)
                logger.info("DLP: moteur Presidio chargé (spaCy %s)", _SPACY_MODEL)
    return _engines


class PresidioMaskingProvider(BaseMaskingProvider):
    """
    Masquage réel via Microsoft Presidio (NER 100% local, spaCy
    fr_core_news_lg — aucun appel réseau, cohérent avec l'exécution locale
    d'Ollama).
    """

    def __init__(self, entities: Optional[list[str]] = None, language: str = _LANGUAGE):
        self.entities = entities or _ENTITIES
        self.language = language

    def mask(self, text: str) -> str:
        if not text:
            return text
        try:
            analyzer, anonymizer = _get_presidio_engines()
            results = analyzer.analyze(text=text, entities=self.entities, language=self.language)
            if not results:
                return text
            anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
            return anonymized.text
        except Exception:
            # Le DLP ne doit jamais faire échouer l'analyse : mieux vaut
            # logger et laisser passer le texte non masqué que de casser
            # tout le pipeline sur un bug d'une dépendance tierce (spaCy).
            # Compromis assumé et documenté (docs/module_2, points
            # d'incertitude) — un vrai déploiement voudrait sans doute
            # l'inverse (bloquer plutôt que fuiter) ; à trancher si le projet
            # sort du prototype.
            logger.exception("DLP: échec du masquage Presidio, texte transmis non masqué")
            return text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_masking_provider() -> BaseMaskingProvider:
    """Factory — lit settings.DLP_MASK_ENABLED (True par défaut, protecteur)."""
    if not settings.DLP_MASK_ENABLED:
        return NoOpMaskingProvider()
    return PresidioMaskingProvider()


__all__ = [
    "BaseMaskingProvider",
    "NoOpMaskingProvider",
    "PresidioMaskingProvider",
    "get_masking_provider",
]
