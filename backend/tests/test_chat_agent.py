"""
Tests unitaires — Module 9 : Chat Agent (app/agents/chat_agent.py)

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest

from app.agents.chat_agent import ChatAgent, ChatAgentError
from app.agents.dlp_masking import BaseMaskingProvider
from app.agents.llm_layer import BaseLLMProvider, LLMTimeoutError
from tests.fakes.llm import FakeLLMProvider
from app.schemas.llm import LLMRequest, LLMResponse, LLMUsage
from app.schemas.session import ChatMessage


class _CapturingProvider(BaseLLMProvider):
    """Capture le dernier LLMRequest reçu — même pattern que
    test_test_design_agent.py::_CapturingProvider."""

    def __init__(self, response_content: str = "[FICTIF] Peux-tu préciser qui utilisera cette fonctionnalité ?"):
        self._response_content = response_content
        self.last_request: LLMRequest | None = None

    @property
    def provider_name(self) -> str:
        return "capturing"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        return LLMResponse(
            content=self._response_content, model="capturing-model", provider=self.provider_name, usage=LLMUsage()
        )


class _UppercaseMaskingProvider(BaseMaskingProvider):
    """Double déterministe : transforme le texte pour qu'on puisse vérifier
    qu'il a bien été passé au masquage avant l'appel LLM."""

    def mask(self, text: str) -> str:
        return text.upper()


class _AlwaysTimingOutProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "always-timing-out"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMTimeoutError("Simulated timeout", provider=self.provider_name)


def _history(*turns: tuple[str, str]) -> list[ChatMessage]:
    from datetime import datetime, timezone

    return [ChatMessage(role=role, content=content, created_at=datetime.now(timezone.utc)) for role, content in turns]


class TestChatAgentReply:
    @pytest.mark.asyncio
    async def test_returns_llm_response_content(self):
        provider = _CapturingProvider("[FICTIF] Quel type d'utilisateur est concerné ?")
        agent = ChatAgent(llm_provider=provider)

        reply = await agent.reply(_history(("user", "[FICTIF] je veux ajouter un paiement par QR code")))

        assert reply == "[FICTIF] Quel type d'utilisateur est concerné ?"

    @pytest.mark.asyncio
    async def test_sends_full_history_to_the_llm(self):
        provider = _CapturingProvider()
        agent = ChatAgent(llm_provider=provider)

        await agent.reply(
            _history(
                ("user", "[FICTIF] je veux ajouter un paiement par QR code"),
                ("assistant", "[FICTIF] Pour quel type d'utilisateur ?"),
                ("user", "[FICTIF] pour les clients particuliers"),
            )
        )

        assert provider.last_request is not None
        assert len(provider.last_request.messages) == 3
        assert provider.last_request.messages[-1].content == "[FICTIF] pour les clients particuliers"

    @pytest.mark.asyncio
    async def test_never_requests_json_format(self):
        """Règle du module 9 : jamais de format structuré côté chat — c'est
        le rôle du module 2, déclenché explicitement par finalize_chat."""
        provider = _CapturingProvider()
        agent = ChatAgent(llm_provider=provider)

        await agent.reply(_history(("user", "[FICTIF] bonjour")))

        assert provider.last_request.response_format != "json"

    @pytest.mark.asyncio
    async def test_masks_user_messages_before_sending(self):
        provider = _CapturingProvider()
        agent = ChatAgent(llm_provider=provider, masking_provider=_UppercaseMaskingProvider())

        await agent.reply(_history(("user", "je m'appelle jad")))

        assert provider.last_request.messages[0].content == "JE M'APPELLE JAD"

    @pytest.mark.asyncio
    async def test_does_not_mask_assistant_messages(self):
        provider = _CapturingProvider()
        agent = ChatAgent(llm_provider=provider, masking_provider=_UppercaseMaskingProvider())

        await agent.reply(
            _history(
                ("user", "je m'appelle jad"),
                ("assistant", "[FICTIF] pour qui est-ce ?"),
            )
        )

        assert provider.last_request.messages[1].content == "[FICTIF] pour qui est-ce ?"

    @pytest.mark.asyncio
    async def test_llm_provider_error_raises_chat_agent_error(self):
        agent = ChatAgent(llm_provider=_AlwaysTimingOutProvider())

        with pytest.raises(ChatAgentError):
            await agent.reply(_history(("user", "[FICTIF] bonjour")))

    @pytest.mark.asyncio
    async def test_works_with_mock_provider_default_response(self):
        """Sanity check bout en bout avec le FakeLLMProvider standard du projet
        (pas de custom_responses -> réponse par défaut)."""
        agent = ChatAgent(llm_provider=FakeLLMProvider())

        reply = await agent.reply(_history(("user", "[FICTIF] bonjour")))

        assert isinstance(reply, str)
        assert reply != ""
