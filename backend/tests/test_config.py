"""
Tests unitaires — configuration (app/config.py)

En APP_ENV=prod, l'application doit refuser de démarrer si la configuration
est incomplète : secret de session de dev, provider LLM non supporté, Jira
ou SSO absents. Les instances Settings sont construites sans lire .env
(`_env_file=None`) pour ne dépendre que des valeurs passées ici.

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import DEV_SESSION_SECRET, Settings

_VALID_PROD = {
    "APP_ENV": "prod",
    "LLM_PROVIDER": "ollama",
    "AUTH_SESSION_SECRET": "x" * 48,
    "JIRA_BASE_URL": "https://fictif.atlassian.net",
    "JIRA_API_TOKEN": "tok",
    "JIRA_USER_EMAIL": "bot@fictif.test",
    "JIRA_PROJECT_KEY": "PROTO",
    "AUTH_OIDC_ISSUER": "https://login.fictif.test/v2.0",
    "AUTH_OIDC_CLIENT_ID": "client",
    "AUTH_OIDC_CLIENT_SECRET": "secret",
}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_VALID_PROD, **overrides})


class TestProductionValidation:
    def test_complete_prod_config_starts(self):
        settings = _settings()

        assert settings.is_production
        assert settings.dev_login_enabled is False

    def test_dev_session_secret_refused(self):
        with pytest.raises(ValidationError, match="AUTH_SESSION_SECRET a encore sa valeur de développement"):
            _settings(AUTH_SESSION_SECRET=DEV_SESSION_SECRET)

    def test_short_session_secret_refused(self):
        with pytest.raises(ValidationError, match="au moins 32 octets"):
            _settings(AUTH_SESSION_SECRET="trop-court")

    def test_mock_llm_provider_refused(self):
        with pytest.raises(ValidationError, match="LLM_PROVIDER='mock' non supporté"):
            _settings(LLM_PROVIDER="mock")

    @pytest.mark.parametrize("name", ["JIRA_BASE_URL", "JIRA_API_TOKEN", "JIRA_USER_EMAIL"])
    def test_missing_jira_refused(self, name):
        with pytest.raises(ValidationError, match=f"{name} manquant"):
            _settings(**{name: None})

    @pytest.mark.parametrize("name", ["AUTH_OIDC_ISSUER", "AUTH_OIDC_CLIENT_ID", "AUTH_OIDC_CLIENT_SECRET"])
    def test_missing_sso_refused(self, name):
        with pytest.raises(ValidationError, match=f"{name} manquant"):
            _settings(**{name: None})

    def test_all_problems_reported_at_once(self):
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None, APP_ENV="prod")

        message = str(exc_info.value)
        assert "AUTH_SESSION_SECRET" in message
        assert "JIRA_BASE_URL" in message
        assert "AUTH_OIDC_ISSUER" in message


class TestNonProductionEnvironments:
    def test_dev_accepts_incomplete_config(self):
        settings = Settings(_env_file=None, APP_ENV="dev")

        assert settings.dev_login_enabled is True

    def test_default_llm_provider_is_not_mock(self):
        assert Settings(_env_file=None).LLM_PROVIDER == "ollama"

    def test_dev_login_disabled_when_sso_configured(self):
        settings = Settings(_env_file=None, APP_ENV="dev", AUTH_OIDC_ISSUER="https://login.fictif.test/v2.0")

        assert settings.dev_login_enabled is False


class TestEnvironmentVariables:
    def test_environment_variables_are_read(self, monkeypatch):
        """Indispensable en conteneur, où il n'y a pas de fichier .env."""
        monkeypatch.setenv("JIRA_PROJECT_KEY", "ENVKEY")

        assert Settings(_env_file=None).JIRA_PROJECT_KEY == "ENVKEY"

    def test_dotenv_wins_over_environment(self, monkeypatch, tmp_path):
        """Une variable globale du poste ne doit pas écraser le .env du projet."""
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=sqlite+aiosqlite:///./projet.db\n", encoding="utf-8")
        monkeypatch.setenv("DATABASE_URL", "postgresql://autre-projet/db")

        assert Settings(_env_file=env_file).DATABASE_URL == "sqlite+aiosqlite:///./projet.db"
