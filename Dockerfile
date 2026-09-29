FROM python:3.12-slim-bookworm AS engine

ARG ENERGYPLUS_VERSION=24.2.0
ARG ENERGYPLUS_SHA=e7ecb2d53b
ARG ENERGYPLUS_URL=https://github.com/NatLabRockies/EnergyPlus/releases/download/v${ENERGYPLUS_VERSION}/EnergyPlus-${ENERGYPLUS_VERSION}-${ENERGYPLUS_SHA}-Linux-Ubuntu22.04-x86_64.tar.gz

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENERGYPLUS_DIR=/opt/EnergyPlus \
    SHELTER_DATA_DIR=/data \
    SHELTER_ENGINE_WORK_DIR=/tmp/shelter_runs

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl tar libgomp1 libx11-6 libexpat1 \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "${ENERGYPLUS_URL}" -o /tmp/energyplus.tar.gz \
 && mkdir -p /opt/EnergyPlus \
 && tar -xzf /tmp/energyplus.tar.gz -C /opt/EnergyPlus --strip-components=1 \
 && rm /tmp/energyplus.tar.gz \
 && test -f /opt/EnergyPlus/Energy+.idd \
 && /opt/EnergyPlus/energyplus --version

WORKDIR /app
COPY engine/pyproject.toml engine/README.md ./engine/
COPY engine/src ./engine/src
COPY api/pyproject.toml ./api/
COPY api/src ./api/src

RUN pip install --no-cache-dir ./engine \
 && pip install --no-cache-dir ./api \
 && python -c "import shelter_api.main, shelter_api.sites, ladybug.epw, timezonefinder, zoneinfo, PIL.Image; zoneinfo.ZoneInfo('Asia/Kolkata')"

COPY engine/tests ./engine/tests
COPY scripts ./scripts

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "shelter_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
