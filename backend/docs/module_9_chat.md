# Module 9 — Chat de clarification

## Rôle

Insère une phase de conversation libre avec le LLM AVANT tout appel au Requirement Analyzer Agent (module 2), pour que l'ingénieur garde la main sur ce qui est généré plutôt que de se voir imposer un résultat structuré + bouton de validation dès la première interaction.

**Contexte (22/07/2026)** : retour utilisateur direct — *"le modèle est strict et confiant [...] même en générant quelque chose d'hallucinant il est confiant en donnant directement le bouton pour valider [...] prolonge le chat avec l'utilisateur qui est le roi comme dans les LLM connus (ChatGPT, Claude...)"*. Le pipeline précédent enchaînait `create_session` → analyse structurée automatique → panneau de validation, sans qu'aucune discussion n'ait eu lieu. Ce module casse cet enchaînement automatique.

## Architecture

```
Backend
  app/agents/chat_agent.py       — ChatAgent.reply(history) -> str (jamais de JSON)
  app/models/session.py          — SessionModel.chat_history (JSON), statut 'chatting'
  app/schemas/session.py         — ChatMessage, ChatSendRequest, SessionStatus += "chatting"
  app/services/session_service.py — create_session (statut initial), send_chat_message,
                                     finalize_chat, _render_chat_transcript
  app/routers/sessions.py        — POST .../chat, POST .../chat/finalize
  app/dependencies.py            — get_chat_agent (overridable en test)
  app/main.py                    — gestionnaire ChatAgentError -> 503

Frontend
  src/components/ChatThread.tsx  — historique complet (bulles), input, bouton "Générer l'analyse"
  src/api.ts                     — sendChatMessage(), finalizeChat()
  src/App.tsx                    — affiche ChatThread quand session.status === "chatting"
```

## Flow

```
POST /api/sessions (user_input, context?, files?)
    -> statut 'chatting', chat_history = [{role: "user", content: user_input}]
       AUCUN appel LLM ici (contrairement à avant le 22/07/2026)

POST /api/sessions/{id}/chat  {"message": "..."}
    -> ChatAgent.reply(chat_history + nouveau message) — appel LLM SYNCHRONE
    -> chat_history += [user, assistant] ; reste en 'chatting'
    -> répétable autant de fois que l'ingénieur le souhaite

POST /api/sessions/{id}/chat/finalize
    -> statut 'analyzing' (kickoff classique, cf. module 4)
    -> tâche de fond : run_requirement_analysis, avec le transcript complet
       de chat_history injecté dans le `context` envoyé au module 2
    -> à partir d'ici, comportement IDENTIQUE à avant le module 9
       (pending_requirement_review / requirement_failed, etc.)
```

## Endpoints

| Méthode | Route | Action |
|---|---|---|
| `POST` | `/api/sessions/{id}/chat` | Ajoute un tour de conversation (400 si hors statut `chatting`) |
| `POST` | `/api/sessions/{id}/chat/finalize` | Sort de `chatting`, lance le module 2 en tâche de fond |

## Choix techniques

1. **Appel `/chat` synchrone, pas de `BackgroundTasks`** — contrairement à TOUS les autres appels LLM du projet (modules 2/3, toujours en tâche de fond avec polling). Justifié : une réponse conversationnelle est courte (quelques secondes avec un modèle local), et l'UX d'un chat *attend* naturellement une réponse avant de continuer, contrairement à une analyse structurée de plusieurs dizaines de secondes à plusieurs minutes. Documenté explicitement dans `session_service.send_chat_message` pour ne pas être "corrigé" par erreur vers le pattern kickoff/run plus tard.
2. **Rien n'est persisté si l'appel LLM échoue** (`send_chat_message`) — ni le message de l'ingénieur, ni de réponse partielle. Permet à l'ingénieur de renvoyer son message sans risque de doublon dans l'historique. `ChatAgentError` remonte telle quelle, convertie en 503 par le gestionnaire enregistré dans `app/main.py` (même principe que `SessionNotFoundError`/`InvalidSessionTransitionError`).
3. **`ChatAgent` ne produit jamais de JSON** — le system prompt l'interdit explicitement, même si on le lui demande. Sépare clairement : le chat *discute*, seul `finalize_chat` déclenche une génération structurée (module 2, JSON validé par schéma + retry). Évite de dupliquer la logique de validation/retry du module 2 dans le chat.
4. **`chat_history` stocké en JSON sur `SessionModel`** (pas de table dédiée) — cohérent avec `tickets` (déjà une liste JSON sur le même modèle). Plus simple qu'une table `ChatMessageModel` séparée pour un historique qui n'a pas besoin d'être interrogé indépendamment de sa session.
5. **Transcript injecté comme `context`, pas comme nouveau champ** — réutilise exactement le mécanisme déjà en place pour les pièces jointes et le `guidance` de régénération (modules 2/8) : un seul point d'entrée masqué par le DLP (`RequirementAnalyzerAgent.analyze`), aucune logique de masquage dupliquée. Omis si la conversation ne contient que le message initial (`_render_chat_transcript` renvoie `None` — déjà couvert par `raw_input`, pas de doublon).
6. **DLP : chaque message `role="user"` masqué à l'appel LLM, jamais au stockage** — même principe que `RequirementAnalyzerAgent.analyze()` : `chat_history` est stocké tel quel en base, seul ce qui part réellement vers Ollama est masqué (cf. `ChatAgent.reply`).
7. **Température plus élevée (0.7 vs 0.3)** — les modules 2/3 utilisent une température basse pour un résultat structuré reproductible ; le chat vise une conversation naturelle, pas de format à respecter.

## Lancer les tests

```bash
cd backend
pytest tests/test_chat_agent.py tests/test_session_service.py::TestChatFlow tests/test_sessions_router.py::TestChatEndpoints -v
```

21 tests : agent (masquage DLP des messages utilisateur, jamais des réponses assistant, aucun `response_format=json`, erreur d'infrastructure), service (transitions d'état, rien de persisté sur échec LLM, transcript injecté ou omis selon le cas), routeur (câblage HTTP, 409 hors statut `chatting`, 503 sur échec LLM sans effet de bord, 422 sur message vide).

## Points d'incertitude

1. **Jamais testé avec un vrai LLM local** (Ollama) — contrairement aux modules 2/3 (testés réellement le 19-22/07/2026), la qualité conversationnelle réelle du system prompt (questions pertinentes, ton naturel, concision) n'a été vérifiée qu'au niveau mécanique (tests avec `FakeLLMProvider`/doubles). À valider en usage réel : le modèle configuré (`llama3.2:3b`, petit) peut avoir plus de mal à respecter des règles de style ("2-4 phrases", "une ou deux questions à la fois") qu'un modèle plus grand.
2. **Pas de limite de longueur sur `chat_history`** — une conversation très longue grossit le prompt envoyé à CHAQUE tour (tout l'historique est renvoyé à chaque appel, cf. `ChatAgent.reply`), avec un risque de dépasser `LLM_DEFAULT_MAX_TOKENS`/le contexte du modèle sur une discussion très prolongée. Aucun troncature/résumé implémenté.
3. **Pas de bouton "annuler" une session en `chatting`** — contrairement aux étapes suivantes (`reject`), une session bloquée en conversation n'a pas de sortie "abandon" autre que `finalize_chat` (même avec un message initial très pauvre) ou l'abandon silencieux côté client (la session reste en `chatting` en base indéfiniment).
4. **`finalize_chat` ne relit pas la conversation pour vérifier qu'elle est "suffisamment" avancée** — l'ingénieur peut cliquer "Générer l'analyse" immédiatement après le tout premier message, sans avoir discuté du tout (comportement volontairement permissif, cf. `ChatThread.tsx` : le bouton n'est jamais désactivé selon la longueur de l'historique).
