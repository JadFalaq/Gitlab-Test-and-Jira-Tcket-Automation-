"""
Configuration centralisée de l'application.

Utilise pydantic-settings pour charger les variables d'environnement
depuis un fichier .env ou l'environnement système.
"""

from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

# Valeur de dev du secret de session : refusée au démarrage en APP_ENV=prod.
DEV_SESSION_SECRET = "dev-insecure-secret-change-me-in-env-please-32-bytes-min"

# Providers LLM réels acceptés en production (cf. app/agents/llm_layer.py).
SUPPORTED_LLM_PROVIDERS: tuple[str, ...] = ("ollama",)


class Settings(BaseSettings):
    """
    Configuration globale du prototype.

    Toutes les valeurs sensibles sont chargées depuis les variables
    d'environnement ou un fichier .env à la racine du backend.
    """

    # --- Application ---
    # "dev"  : poste du développeur (dev-login autorisé)
    # "test" : suite pytest uniquement
    # "prod" : toute instance utilisée par d'autres personnes — le démarrage
    #          échoue si la configuration est incomplète (cf. _check_production)
    APP_ENV: Literal["dev", "test", "prod"] = Field(default="dev", description="Environnement d'exécution")
    APP_NAME: str = "Test Automation Agent"
    APP_VERSION: str = "0.1.0-prototype"
    DEBUG: bool = True

    # --- LLM Provider ---
    # Valeurs supportées : voir SUPPORTED_LLM_PROVIDERS. Le faux provider des
    # tests vit dans tests/fakes/ et n'est jamais disponible dans l'application.
    LLM_PROVIDER: str = Field(default="ollama", description="LLM provider to use")

    # --- Ollama ---
    OLLAMA_BASE_URL: str = Field(default="http://localhost:11434", description="Ollama API base URL")
    OLLAMA_MODEL: str = Field(default="llama3.1:8b", description="Ollama model name")
    OLLAMA_TIMEOUT_SECONDS: int = Field(default=120, description="Timeout for Ollama API calls")

    # --- LLM Defaults ---
    LLM_DEFAULT_TEMPERATURE: float = Field(default=0.3, description="Low temperature for structured output")
    LLM_DEFAULT_MAX_TOKENS: int = Field(default=4096, description="Max tokens per response")
    LLM_MAX_RETRIES: int = Field(default=2, description="Max retries on LLM parse failure")

    # --- Module 3 (Test Design Agent) ---
    # Plafond dédié, plus haut que LLM_DEFAULT_MAX_TOKENS : depuis le
    # renforcement de la couverture (22/07/2026 — happy path + cas négatif +
    # cas limite par critère fonctionnel), la sortie JSON attendue est
    # sensiblement plus longue qu'avant (plusieurs scénarios par feature
    # plutôt qu'un seul) — un plafond partagé avec le module 2 risquerait de
    # tronquer la réponse avant la fin du JSON.
    TEST_DESIGN_MAX_TOKENS: int = Field(
        default=6144, description="Max tokens pour la génération de tests (module 3), plus haut que le défaut LLM"
    )

    # --- Database ---
    # Préfixe "sqlite+aiosqlite" requis : le module 4 utilise SQLAlchemy en mode async.
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./test_automation.db",
        description="Database URL (async, via aiosqlite pour SQLite)",
    )

    # --- Jira (compte de service) ---
    JIRA_BASE_URL: Optional[str] = Field(default=None, description="Jira Cloud sandbox URL")
    JIRA_API_TOKEN: Optional[str] = Field(default=None, description="Jira API token du compte de service")
    JIRA_USER_EMAIL: Optional[str] = Field(default=None, description="Jira user email for auth")
    JIRA_PROJECT_KEY: str = Field(default="PROTO", description="Default Jira project key (fictitious)")

    # --- CORS (for frontend dev server) ---
    CORS_ORIGINS: list[str] = Field(default=["http://localhost:5173", "http://localhost:3000"])

    # --- Tracking (module 6) ---
    TRACKING_SYNC_INTERVAL_SECONDS: int = Field(
        default=15, description="Intervalle entre deux rafraîchissements de statut sur le flux SSE de suivi"
    )

    # --- DLP (masquage des données personnelles avant tout envoi externe) ---
    # True par défaut = protecteur par défaut (cf. app/agents/dlp_masking.py).
    # Désactivé explicitement dans toute la suite de tests (tests/conftest.py)
    # pour rester déterministe et ne pas payer le chargement du modèle spaCy.
    DLP_MASK_ENABLED: bool = Field(
        default=True,
        description="Masque PERSON/LOCATION/ORGANIZATION/EMAIL/PHONE/IBAN avant envoi au LLM (Presidio + spaCy fr_core_news_lg)",
    )

    # --- RAG (few-shot sur les générations passées acceptées, module 2/4) ---
    # Nécessite un modèle d'embedding tiré dans Ollama : ollama pull nomic-embed-text
    # Best-effort : un échec (Ollama down, modèle absent) fait juste continuer
    # sans exemples few-shot, ne casse jamais l'analyse (cf. rag_service.py).
    # Désactivé explicitement dans toute la suite de tests (tests/conftest.py).
    RAG_ENABLED: bool = Field(
        default=True, description="Active le RAG few-shot (retrouve des générations passées similaires)"
    )
    EMBEDDING_MODEL: str = Field(default="nomic-embed-text", description="Modèle Ollama utilisé pour les embeddings")
    RAG_TOP_K: int = Field(default=3, description="Nombre max d'exemples few-shot injectés dans le prompt")
    # Calibré empiriquement le 21/07/2026 (nomic-embed-text, phrases courtes
    # en français) : une requête SANS RAPPORT avec l'historique a mesuré une
    # similarité de 0.63, une requête VRAIMENT similaire 0.79 — le plancher
    # de similarité de ce modèle est élevé sur du texte court, un seuil bas
    # (ex: 0.5) ne filtre donc rien. 0.72 sépare les deux cas mesurés avec
    # une marge raisonnable. Calibrage sur UN SEUL point de données : à
    # réajuster avec l'usage réel une fois plus de générations 'accepted'
    # accumulées (cf. docs/module_2_requirement_analyzer.md).
    RAG_MIN_SIMILARITY: float = Field(
        default=0.72, description="Similarité cosinus minimale pour qu'un exemple passé soit retenu"
    )

    # --- Pièces jointes (module 2, extension : photos/documents à la création) ---
    # Documents (pdf/docx/xlsx/pptx/txt/md) : extraction de texte, aucun
    # modèle requis, toujours active. Images : description factuelle via un
    # modèle vision Ollama, désactivable (VISION_ENABLED) si non tiré.
    UPLOAD_DIR: str = Field(default="uploads", description="Dossier de stockage des pièces jointes (relatif à backend/)")
    MAX_ATTACHMENT_SIZE_MB: int = Field(default=15, description="Taille max par pièce jointe")
    VISION_ENABLED: bool = Field(
        default=True,
        description="Décrit les images jointes via un modèle vision Ollama (ollama pull moondream)",
    )
    VISION_MODEL: str = Field(default="moondream", description="Modèle Ollama utilisé pour décrire les images")

    # --- Authentification SSO (module 8, multi-utilisateur) ---
    # OIDC générique : fonctionne tel quel avec Google Workspace
    # (issuer="https://accounts.google.com") ou Microsoft Entra ID
    # (issuer="https://login.microsoftonline.com/<tenant-id>/v2.0"), les deux
    # exposant le même document de découverte standard — voir
    # app/auth/oidc.py. Tous les comptes partagent le même compte de
    # service Jira (settings.JIRA_* ci-dessus) : pas de lien individuel
    # compte SSO <-> compte Jira.
    AUTH_OIDC_ISSUER: Optional[str] = Field(
        default=None, description="URL d'issuer OIDC du fournisseur SSO de l'entreprise"
    )
    AUTH_OIDC_CLIENT_ID: Optional[str] = Field(
        default=None, description="Client ID de l'app OAuth enregistrée côté fournisseur SSO"
    )
    AUTH_OIDC_CLIENT_SECRET: Optional[str] = Field(
        default=None, description="Client secret de l'app OAuth (jamais commité, .env uniquement)"
    )
    AUTH_REDIRECT_URI: str = Field(
        default="http://localhost:8000/api/auth/callback",
        description="URI de callback à enregistrer côté fournisseur SSO",
    )
    FRONTEND_URL: str = Field(
        default="http://localhost:5173", description="URL du frontend, pour la redirection post-login"
    )
    AUTH_SESSION_SECRET: str = Field(
        default=DEV_SESSION_SECRET,
        description="Clé de signature du cookie de session applicatif (JWT) — À CHANGER en .env, jamais la valeur par défaut hors dev local",
    )
    AUTH_SESSION_MAX_AGE_SECONDS: int = Field(
        default=60 * 60 * 24 * 7, description="Durée de validité du cookie de session (7 jours par défaut)"
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        # Priorité : .env du projet > variables d'environnement > défauts.
        # - En conteneur (Docker, PaaS), il n'y a pas de .env : la config vient
        #   des variables d'environnement.
        # - Sur un poste de dev, le .env du projet gagne : une variable globale
        #   du poste (ex: DATABASE_URL d'un autre projet) ne doit pas le
        #   court-circuiter.
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)

    @property
    def dev_login_enabled(self) -> bool:
        """Connexion sans SSO : jamais en prod, jamais quand un SSO est configuré."""
        return self.APP_ENV in ("dev", "test") and not self.AUTH_OIDC_ISSUER

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "prod"

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        """En APP_ENV=prod, refuse de démarrer si la configuration est incomplète."""
        if self.APP_ENV != "prod":
            return self

        problems: list[str] = []
        if self.AUTH_SESSION_SECRET == DEV_SESSION_SECRET:
            problems.append("AUTH_SESSION_SECRET a encore sa valeur de développement")
        elif len(self.AUTH_SESSION_SECRET.encode("utf-8")) < 32:
            problems.append("AUTH_SESSION_SECRET doit faire au moins 32 octets")
        if self.LLM_PROVIDER not in SUPPORTED_LLM_PROVIDERS:
            problems.append(
                f"LLM_PROVIDER='{self.LLM_PROVIDER}' non supporté (attendu : {', '.join(SUPPORTED_LLM_PROVIDERS)})"
            )
        for name in ("JIRA_BASE_URL", "JIRA_API_TOKEN", "JIRA_USER_EMAIL", "JIRA_PROJECT_KEY"):
            if not getattr(self, name):
                problems.append(f"{name} manquant (Jira est obligatoire en production)")
        for name in ("AUTH_OIDC_ISSUER", "AUTH_OIDC_CLIENT_ID", "AUTH_OIDC_CLIENT_SECRET"):
            if not getattr(self, name):
                problems.append(f"{name} manquant (le SSO est obligatoire en production)")

        if problems:
            # Pydantic remonte ce ValueError en ValidationError à la construction
            # de `settings` : l'import de app.config, donc le démarrage, échoue.
            raise ValueError(
                "Configuration de production incomplète :\n- " + "\n- ".join(problems)
            )
        return self


# Singleton instance
settings = Settings()
