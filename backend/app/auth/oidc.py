"""
Module 8 — Authentification SSO : couche d'abstraction OIDC.

Authentification entièrement déléguée à un fournisseur OIDC externe (Google
Workspace ou Microsoft Entra ID — les deux exposent un document de
découverte standard `{issuer}/.well-known/openid-configuration`, une seule
implémentation générique suffit donc, pilotée par settings.AUTH_OIDC_ISSUER).
Aucun mot de passe n'est géré par cette application (cf. UserModel).

Design decisions:
    - Flow "Authorization Code" standard, client confidentiel (secret côté
      serveur uniquement, jamais exposé au frontend) — pas de PKCE pour ce
      prototype.
    - Le document de découverte est récupéré à la demande, jamais mis en
      cache longue durée : le prototype ne redémarre pas assez souvent pour
      que le coût compte, et ça évite un état obsolète si le fournisseur
      change ses endpoints.
    - L'id_token renvoyé par l'échange du code est TOUJOURS vérifié :
      signature (clés publiques JWKS du fournisseur, RS256), émetteur
      (`iss` = issuer du document de découverte), audience (`aud` = notre
      client_id) et expiration (`exp`, 60 s de tolérance d'horloge). Le `sub`
      vérifié fait foi ; /userinfo complète seulement l'email et le nom
      (Entra ID ne met pas toujours l'email dans l'id_token) et doit
      renvoyer le même `sub`.
    - Pas de `nonce` : le code n'est échangé que côté serveur avec le
      client_secret, et le `state` anti-CSRF est vérifié par le router.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import NamedTuple, Optional
from urllib.parse import urlencode

import httpx
import jwt

from app.config import settings

logger = logging.getLogger(__name__)


class AuthProviderError(Exception):
    """Échec d'authentification (config manquante, code invalide, fournisseur SSO injoignable)."""


# Algorithme de signature des id_token de Google et de Microsoft Entra ID.
# Liste fermée : jamais "none" ni un algorithme symétrique (HS256), qui
# permettraient de forger un token sans la clé privée du fournisseur.
_ID_TOKEN_ALGORITHMS = ["RS256"]
_CLOCK_SKEW_SECONDS = 60


class AuthenticatedUser(NamedTuple):
    """Identité renvoyée par le fournisseur SSO après un login réussi."""

    email: str
    display_name: str
    subject: str  # "sub" OIDC : identifiant stable côté fournisseur


class BaseAuthProvider(ABC):
    """Interface abstraite — voir CLAUDE.md « Providers interchangeables »."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        """URL vers laquelle rediriger l'utilisateur pour démarrer le login SSO."""
        ...

    @abstractmethod
    async def fetch_user(self, code: str, redirect_uri: str) -> AuthenticatedUser:
        """Échange le code d'autorisation contre l'identité de l'utilisateur."""
        ...


class OidcAuthProvider(BaseAuthProvider):
    """
    Client OIDC générique (Authorization Code flow), compatible avec tout
    fournisseur exposant un document de découverte standard : Google
    Workspace (`issuer="https://accounts.google.com"`) ou Microsoft Entra ID
    (`issuer="https://login.microsoftonline.com/<tenant-id>/v2.0"`).
    """

    def __init__(
        self,
        issuer: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self._issuer = issuer if issuer is not None else settings.AUTH_OIDC_ISSUER
        self._client_id = client_id if client_id is not None else settings.AUTH_OIDC_CLIENT_ID
        self._client_secret = client_secret if client_secret is not None else settings.AUTH_OIDC_CLIENT_SECRET
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return "oidc"

    def _require_config(self) -> None:
        if not (self._issuer and self._client_id and self._client_secret):
            raise AuthProviderError(
                "SSO non configuré : AUTH_OIDC_ISSUER / AUTH_OIDC_CLIENT_ID / "
                "AUTH_OIDC_CLIENT_SECRET manquants (voir backend/.env.example)"
            )

    async def _discover(self) -> dict:
        self._require_config()
        assert self._issuer is not None  # pour le type-checker : déjà vérifié par _require_config
        url = f"{self._issuer.rstrip('/')}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AuthProviderError(
                    f"Document de découverte OIDC injoignable ({url}) : {exc}"
                ) from exc
        return response.json()

    async def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        discovery = await self._discover()
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
        return f"{discovery['authorization_endpoint']}?{urlencode(params)}"

    async def fetch_user(self, code: str, redirect_uri: str) -> AuthenticatedUser:
        discovery = await self._discover()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                token_response = await client.post(
                    discovery["token_endpoint"],
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
                token_response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AuthProviderError(f"Échec de l'échange du code d'autorisation : {exc}") from exc

            tokens = token_response.json()
            access_token = tokens.get("access_token")
            if not access_token:
                raise AuthProviderError("Réponse du fournisseur SSO sans access_token")
            id_token = tokens.get("id_token")
            if not id_token:
                raise AuthProviderError("Réponse du fournisseur SSO sans id_token")

            claims = await self._verify_id_token(client, discovery, id_token)

            try:
                userinfo_response = await client.get(
                    discovery["userinfo_endpoint"],
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo_response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AuthProviderError(f"Échec de récupération du profil utilisateur : {exc}") from exc

        userinfo = userinfo_response.json()
        subject = str(claims["sub"])
        if "sub" in userinfo and str(userinfo["sub"]) != subject:
            raise AuthProviderError("Le profil /userinfo ne correspond pas à l'id_token vérifié")

        email = claims.get("email") or userinfo.get("email")
        if not email:
            raise AuthProviderError("Le fournisseur SSO n'a renvoyé aucun email pour cet utilisateur")

        return AuthenticatedUser(
            email=email,
            display_name=claims.get("name") or userinfo.get("name") or email,
            subject=subject,
        )

    async def _verify_id_token(self, client: httpx.AsyncClient, discovery: dict, id_token: str) -> dict:
        """Vérifie signature + iss/aud/exp de l'id_token, retourne ses claims.

        Raises:
            AuthProviderError: token invalide, clé inconnue ou JWKS injoignable.
        """
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise AuthProviderError(f"id_token illisible : {exc}") from exc

        try:
            jwks_response = await client.get(discovery["jwks_uri"])
            jwks_response.raise_for_status()
            key_set = jwt.PyJWKSet.from_dict(jwks_response.json())
        except (httpx.HTTPError, KeyError) as exc:
            raise AuthProviderError(f"Clés publiques du fournisseur SSO injoignables : {exc}") from exc
        except jwt.PyJWTError as exc:
            raise AuthProviderError(f"Clés publiques du fournisseur SSO invalides : {exc}") from exc

        signing_key = next((k for k in key_set.keys if k.key_id == header.get("kid")), None)
        if signing_key is None:
            raise AuthProviderError("id_token signé par une clé inconnue du fournisseur SSO")

        try:
            return jwt.decode(
                id_token,
                key=signing_key.key,
                algorithms=_ID_TOKEN_ALGORITHMS,
                audience=self._client_id,
                issuer=discovery.get("issuer", self._issuer),
                leeway=_CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthProviderError(f"id_token invalide : {exc}") from exc


def get_auth_provider() -> BaseAuthProvider:
    """Dépendance FastAPI par défaut — surchargée en test (cf. app/dependencies.py)."""
    return OidcAuthProvider()


__all__ = [
    "AuthProviderError",
    "AuthenticatedUser",
    "BaseAuthProvider",
    "OidcAuthProvider",
    "get_auth_provider",
]
