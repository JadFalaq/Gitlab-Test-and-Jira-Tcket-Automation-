"""
Configuration centralisée de l'application.

Utilise pydantic-settings pour charger les variables d'environnement
depuis un fichier .env ou l'environnement système.
"""

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    """
    Configuration globale du prototype.

    Toutes les valeurs sensibles sont chargées depuis les variables
    d'environnement ou un fichier .env à la racine du backend.
    """

    # --- Application ---
    APP_NAME: str = "Test Automation Agent"
    APP_VERSION: str = "0.1.0-prototype"
    DEBUG: bool = True

    # --- LLM Provider ---
    # Valeurs supportées : "ollama", "mock"
    # "mock" renvoie des réponses pré-définies (pour les tests)
    LLM_PROVIDER: str = Field(default="mock", description="LLM provider to use: 'ollama' or 'mock'")

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

    # --- Jira (sandbox/mock) ---
    JIRA_BASE_URL: Optional[str] = Field(default=None, description="Jira Cloud sandbox URL")
    JIRA_API_TOKEN: Optional[str] = Field(default=None, description="Jira API token (never use production!)")
    JIRA_USER_EMAIL: Optional[str] = Field(default=None, description="Jira user email for auth")
    JIRA_PROJECT_KEY: str = Field(default="PROTO", description="Default Jira project key (fictitious)")

    # --- GitLab (sandbox/mock) ---
    GITLAB_BASE_URL: Optional[str] = Field(default=None, description="GitLab sandbox URL")
    GITLAB_PRIVATE_TOKEN: Optional[str] = Field(default=None, description="GitLab private token")
    GITLAB_PROJECT_ID: Optional[str] = Field(default=None, description="GitLab project ID")

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
    # app/agents/auth_layer.py. Tous les comptes partagent le même provider
    # Jira/GitLab (settings.JIRA_*/GITLAB_* ci-dessus) : pas de lien
    # individuel compte SSO <-> compte Jira.
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
        default="dev-insecure-secret-change-me-in-env-please-32-bytes-min",
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
        # Exclut délibérément `env_settings` (variables d'environnement du
        # process/shell) : une variable globale comme DATABASE_URL, définie
        # par un autre projet sur la machine, ne doit jamais pouvoir
        # court-circuiter le .env de CE projet. Seul .env (ou les valeurs
        # par défaut ci-dessus) pilote la config ici.
        return (init_settings, dotenv_settings, file_secret_settings)


# Singleton instance
settings = Settings()
