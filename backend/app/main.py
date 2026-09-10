"""
FastAPI application — Point d'entrée principal.

Pour l'instant, ce fichier configure l'app FastAPI avec :
- CORS pour le frontend de développement
- Un endpoint de health check
- Le routeur chat (sera connecté au pipeline complet plus tard)

Démarrage : uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.agents.chat_agent import ChatAgentError
from app.agents.llm_layer import get_llm_provider
from app.database import init_db
from app.routers.analytics import router as analytics_router
from app.routers.auth import router as auth_router
from app.routers.sessions import router as sessions_router
from app.routers.tickets import router as tickets_router
from app.routers.tracking import router as tracking_router
from app.services.session_service import InvalidSessionTransitionError, SessionNotFoundError


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Prototype — Agent de Génération de Tests & Tickets Jira. "
        "⚠️ Toutes les données sont fictives."
    ),
    lifespan=lifespan,
)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Gestionnaires d'erreurs (module 4) ---
@app.exception_handler(SessionNotFoundError)
async def session_not_found_handler(request: Request, exc: SessionNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(InvalidSessionTransitionError)
async def invalid_transition_handler(
    request: Request, exc: InvalidSessionTransitionError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ChatAgentError)
async def chat_agent_error_handler(request: Request, exc: ChatAgentError) -> JSONResponse:
    """Module 9 — échec d'infrastructure du chat (LLM injoignable). Rien
    n'est persisté côté service (cf. session_service.send_chat_message) :
    l'ingénieur peut renvoyer son message sans risque de doublon."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# --- Routers ---
app.include_router(auth_router)  # module 8 — /api/auth/* (SSO)
app.include_router(sessions_router)
app.include_router(tickets_router)  # module 5 — endpoints /api/sessions/{id}/tickets/*
app.include_router(tracking_router)  # module 6 — /api/tracking/*
app.include_router(analytics_router)  # module 7 — /api/analytics/*


# --- Health Check ---
@app.get("/health", tags=["system"])
async def health_check():
    """
    Vérifie que le backend et le LLM provider sont fonctionnels.
    """
    provider = get_llm_provider()
    llm_healthy = await provider.health_check()

    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "llm_provider": provider.provider_name,
        "llm_healthy": llm_healthy,
    }


@app.get("/", tags=["system"])
async def root():
    return {
        "message": f"{settings.APP_NAME} v{settings.APP_VERSION}",
        "docs": "/docs",
        "health": "/health",
    }
