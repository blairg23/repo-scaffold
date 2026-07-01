FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==1.8.4

ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_IN_PROJECT=false
ENV POETRY_VIRTUALENVS_PATH=/venv

WORKDIR /tmp/bootstrap
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root

WORKDIR /repo

CMD ["bash"]
