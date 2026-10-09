# Module 1 — LLM Abstraction Layer

## Rôle

Isole le reste du système du provider LLM. Tout appel au LLM passe par cette couche, qui traduit des `LLMRequest` / `LLMResponse` normalisés vers l'API native du provider configuré.

## Architecture

```
BaseLLMProvider (ABC)
└── OllamaProvider   — appelle Ollama via POST /api/chat
```

Le faux provider des tests (`FakeLLMProvider`) vit dans `tests/fakes/llm.py` : il n'est jamais enregistré dans l'application (plan d'action, semaine 1).

**Factory** : `get_llm_provider(name)` retourne le bon provider selon la variable `LLM_PROVIDER`.

## Schémas d'interface

| Schéma | Direction | Champs principaux |
|--------|-----------|-------------------|
| `LLMRequest` | Input | `messages`, `system_prompt`, `temperature`, `max_tokens`, `response_format` |
| `LLMResponse` | Output | `content`, `model`, `provider`, `usage`, `raw_response` |
| `LLMMessage` | Sub | `role` (system/user/assistant), `content` |
| `LLMUsage` | Sub | `prompt_tokens`, `completion_tokens`, `total_tokens` |

## Choix techniques

1. **Async par défaut** — FastAPI est async, toute la stack LLM l'est aussi.
2. **FakeLLMProvider (tests uniquement)** — Permet de tester le pipeline complet sans LLM réel. Matche des mots-clés dans le prompt pour choisir la réponse : `"requirement analyzer agent"` / `"test design agent"` matchent directement les system prompts réels des modules 2 et 3. `custom_responses` (passé au constructeur) est toujours prioritaire sur les réponses par défaut. Il n'est pas disponible dans l'application : `LLM_PROVIDER=mock` est refusé.
3. **OllamaProvider** — Utilise `httpx` (async) pour appeler `POST /api/chat` ([doc Ollama](https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-chat-completion)).
4. **Exceptions typées** — `LLMTimeoutError`, `LLMConnectionError`, `LLMModelNotFoundError` pour que les modules appelants puissent réagir spécifiquement.
5. **Registre extensible** — `register_provider()` permet d'ajouter un provider sans modifier `llm_layer.py`.
6. **Aucune exception httpx ne traverse la couche** — `OllamaProvider.complete()` capture les cas spécifiques (timeout, connexion, statut HTTP) puis un filet `httpx.HTTPError` convertit toute erreur transport restante (`ReadError`, `RemoteProtocolError`, ...) en `LLMConnectionError` ; `health_check()` ne lève jamais (toute erreur httpx = `False`). Les agents des modules 2/3 ne voient donc que des `LLMProviderError` typées, jamais un 500 brut.

## Hypothèses

- Ollama est installé localement et le modèle `llama3.1:8b` est téléchargé (`ollama pull llama3.1:8b`).
- Le streaming n'est pas utilisé en phase prototype (réponse complète en une fois).

## Exemple d'exécution

```python
import asyncio
from app.agents.llm_layer import get_llm_provider
from app.schemas.llm import LLMRequest, LLMMessage

async def demo():
    # Nécessite Ollama démarré et le modèle OLLAMA_MODEL téléchargé
    provider = get_llm_provider("ollama")

    request = LLMRequest(
        messages=[LLMMessage(role="user", content="Ajouter un paiement QR code")],
        system_prompt="Tu dois analyze_requirement et retourner du JSON.",
        response_format="json",
    )

    response = await provider.complete(request)
    print(f"Provider: {response.provider}")
    print(f"Model: {response.model}")
    print(f"Tokens: {response.usage.total_tokens}")
    print(f"Response:\n{response.content[:200]}...")

asyncio.run(demo())
```

**Sortie attendue** (exemple, le contenu dépend du modèle) :
```
Provider: ollama
Model: llama3.2:3b
Tokens: 412
Response:
{
  "title": "[FICTIF] Paiement QR Code — App Mobile",
  "type": "feature",
  ...
}
```

## Lancer les tests

```bash
cd backend
..\..\venv\Scripts\activate   # venv partagé, situé à la racine de Projets/
pip install -r requirements.txt
pytest tests/test_llm_layer.py -v
```

## Points d'incertitude

1. **Modèle Ollama** : `llama3.1:8b` est le défaut. Si tu utilises un autre modèle, modifie `OLLAMA_MODEL` dans `.env`.
2. **Streaming** : Non implémenté. Si l'UX nécessite du streaming plus tard, il faudra ajouter une méthode `stream()` au provider.
3. **Rate limiting** : Pas de rate limiting en prototype. À ajouter si on passe sur un provider cloud payant.
