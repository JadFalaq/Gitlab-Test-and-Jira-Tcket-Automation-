# Image du backend FastAPI. Construite par docker-compose.yml (racine).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Modèle spaCy français (~570 Mo, requis par le DLP) dans sa propre couche :
# modifier requirements.txt ne le re-télécharge pas.
RUN pip install "spacy>=3.8,<3.9" \
    && python -m spacy download fr_core_news_lg

# Dépendances (couche mise en cache tant que requirements.txt ne change pas).
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Utilisateur non-root ; /data/uploads est monté en volume (pièces jointes).
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/uploads \
    && chown -R app:app /data
USER app

ENV UPLOAD_DIR=/data/uploads
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Un seul worker : les tâches LLM tournent en arrière-plan dans le process
# (BackgroundTasks) jusqu'à l'arrivée du worker arq (semaine 3 du plan).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
