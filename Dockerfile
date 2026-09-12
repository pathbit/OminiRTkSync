# ==============================================================================
# OminiRTKSync: OmniRoute Universal Token & Connection Sync
# Imagem oficial baseada em Python 3.14 Alpine
# ==============================================================================

FROM python:3.14-alpine

LABEL org.opencontainers.image.title="OminiRTKSync"
LABEL org.opencontainers.image.description="OminiRoute Universal Token & Connection Synchronizer"
LABEL org.opencontainers.image.authors="Eliel Sousa <eliel@pathbit.co>"
LABEL org.opencontainers.image.source="https://github.com/pathbit/OminiRTkSync"

WORKDIR /app

# Criação obrigatória e isolada do Virtual Environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV DB_PATH=/app/data/storage.sqlite
ENV OMNIROUTE_URL=http://127.0.0.1:20128
ENV SYNC_INTERVAL=300
ENV REFRESH_MARGIN=900
ENV WEB_PORT=9191
ENV WEB_HOST=0.0.0.0
ENV ENABLE_WEB_DASHBOARD=1

COPY src/ /app/src/
COPY pyproject.toml /app/

# Instalação do pacote dentro do virtual environment
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

EXPOSE 9191

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD /opt/venv/bin/python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9191/healthz', timeout=3)" || exit 1

ENTRYPOINT ["/opt/venv/bin/python3", "-m", "omini_rtksync.cli"]
CMD ["--daemon"]
