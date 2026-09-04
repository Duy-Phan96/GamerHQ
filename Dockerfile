FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt \
    && useradd --create-home --uid 10001 gamerhq

COPY --chown=gamerhq:gamerhq . /app
USER gamerhq

CMD ["python", "bot.py"]
