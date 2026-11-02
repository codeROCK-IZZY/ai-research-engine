# One image, two roles: API (default CMD) and worker (`python -m app.cli worker`).
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Tesseract OCR (+ French/Spanish language data) is the only system dependency.
# Debian mirrors are used over HTTPS. Optional build secret `extra_ca` supports networks
# with a TLS-inspecting proxy:  docker build --secret id=extra_ca,src=ca.crt ...  (not stored in the image)
RUN --mount=type=secret,id=extra_ca,required=false,mode=0444 \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && CA_OPT="" && if [ -f /run/secrets/extra_ca ]; then CA_OPT="-o Acquire::https::CAInfo=/run/secrets/extra_ca"; fi \
    && apt-get $CA_OPT update \
    && apt-get $CA_OPT install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=extra_ca,required=false,mode=0444 \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements.txt
# Bake the embedding and re-ranking models into the image so containers never download at runtime.
ENV RKE_EMBEDDING_CACHE_DIR=/opt/models
RUN --mount=type=secret,id=extra_ca,required=false,mode=0444 \
    if [ -f /run/secrets/extra_ca ]; then export REQUESTS_CA_BUNDLE=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/models'); from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2', cache_dir='/opt/models')" \
    && chmod -R a+rX /opt/models
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY app ./app
RUN useradd --uid 10001 --create-home rke && mkdir -p /app/var && chown -R rke /app/var
USER rke
# Limit per-process threads: API and worker share the host's cores (see config.embedding_threads).
ENV RKE_FILE_DIR=/app/var/files HF_HUB_OFFLINE=1 RKE_EMBEDDING_THREADS=2 OMP_THREAD_LIMIT=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health/live', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "2"]
