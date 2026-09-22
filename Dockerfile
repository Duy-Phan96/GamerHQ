FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --requirement requirements.lock \
    && pip check \
    && groupadd --gid 10001 gamerhq \
    && useradd --create-home --uid 10001 --gid 10001 gamerhq

# Copy only reviewed application sources; local runtime/private files stay out.
COPY --chown=gamerhq:gamerhq bot.py config.py release_info.py VERSION ./
COPY --chown=gamerhq:gamerhq cogs/ ./cogs/
COPY --chown=gamerhq:gamerhq services/ ./services/
COPY --chown=gamerhq:gamerhq database/ ./database/
COPY --chown=gamerhq:gamerhq tools/ ./tools/
COPY --chown=gamerhq:gamerhq data/games_seed.json ./data/games_seed.json
USER gamerhq

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-m", "tools.container_health"]

CMD ["python", "bot.py"]
