# OminiRTKSync — guardião de conexões do OmniRoute (fonte vendored via git
# subtree de pathbit/OminiRTkSync). A imagem base é pinada por digest como
# qualquer outra dependência externa (contrato SEC-01).
FROM python:3.14-alpine@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc

LABEL org.opencontainers.image.title="TalqueeAI OmniRTKSync"
LABEL org.opencontainers.image.description="OminiRTKSync com base pinada por digest e painel restrito ao loopback"
LABEL org.opencontainers.image.version="1.0.0-talquee.1"

WORKDIR /app

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DB_PATH=/app/data/storage.sqlite \
    OMNIROUTE_URL=http://omniroute:20128 \
    SYNC_INTERVAL=300 \
    REFRESH_MARGIN=900 \
    WEB_PORT=9090 \
    WEB_HOST=127.0.0.1 \
    ENABLE_WEB_DASHBOARD=1

COPY apps/ominirtksync/src/ /app/src/
COPY apps/ominirtksync/pyproject.toml /app/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

EXPOSE 9090

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD /opt/venv/bin/python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz', timeout=3)" || exit 1

ENTRYPOINT ["/opt/venv/bin/python3", "-m", "omini_rtksync.cli"]
CMD ["--daemon"]
