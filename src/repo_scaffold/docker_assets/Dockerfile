# repo-scaffold's own workspace image -- one generic image used to work on any
# managed repo, regardless of that repo's language stack. Not generated from,
# or dependent on, anything in the target repo. See #296.
FROM golang:1-bookworm AS go-toolchain
FROM node:22-bookworm AS node-toolchain

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==1.8.4
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_IN_PROJECT=false
ENV POETRY_VIRTUALENVS_PATH=/venv

COPY --from=go-toolchain /usr/local/go /usr/local/go
ENV GOPATH=/go
ENV PATH="/usr/local/go/bin:${GOPATH}/bin:${PATH}"

COPY --from=node-toolchain /usr/local/bin/node /usr/local/bin/node
COPY --from=node-toolchain /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

WORKDIR /

CMD ["bash"]
