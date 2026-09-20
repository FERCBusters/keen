FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    python3-poetry \
    python3-venv \
    poppler-utils \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* /app/

# Make poetry put the venv in /app/.venv and use the image's Python
RUN poetry config virtualenvs.in-project true \
 && poetry env use /usr/local/bin/python \
 && if [ -f poetry.lock ]; then \
      poetry install --no-interaction --no-ansi --no-root; \
    else \
      poetry lock --no-interaction --no-ansi \
   && poetry install --no-interaction --no-ansi --no-root; \
    fi

# Make the venv the default Python for runtime
ENV PATH="/app/.venv/bin:$PATH"

# App source
COPY app /app/app
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY scripts /app/scripts

EXPOSE 8080

RUN chmod +x /app/scripts/start.sh

# create non-root user
RUN addgroup --system --gid 10001 keen \
 && adduser  --system --uid 10001 --ingroup keen --home /home/keen keen \
 && mkdir -p /var/lib/keen \
 && chown -R keen:keen /app /var/lib/keen

USER keen
ENV HOME=/home/keen

CMD ["/app/scripts/start.sh"]
