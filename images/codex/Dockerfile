FROM node:24-trixie-slim@sha256:50c3b2f6988dfc307b86e5301d69611af31f4789bdf232863b07d3b02fe55ae0

ARG CODEX_VERSION=0.153.4
ARG CODEX_INSTALLER_SHA256=ba92dd27e5c06f0d3bbc58bfa4b9cfb6599cd2742fbb1f92a2765e6c07dedb5a

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       bash build-essential ca-certificates curl gh git jq less openssh-client \
       procps python3 python3-pip python3-venv ripgrep tini unzip \
    && rm -rf /var/lib/apt/lists/*

# Keep the official standalone layout, including its bundled executables.
# The installer verifies release asset digests; pin the installer itself too.
RUN curl -fsSL https://chatgpt.com/codex/install.sh -o /tmp/install-codex.sh \
    && echo "${CODEX_INSTALLER_SHA256}  /tmp/install-codex.sh" | sha256sum -c - \
    && CODEX_HOME=/opt/codex CODEX_INSTALL_DIR=/usr/local/bin CODEX_NON_INTERACTIVE=1 \
       sh /tmp/install-codex.sh --release "${CODEX_VERSION}" \
    && rm /tmp/install-codex.sh \
    && codex --version

RUN groupadd --gid 20213 codex \
    && useradd --uid 20213 --gid 20213 --create-home --shell /bin/bash codex \
    && chmod 700 /home/codex

COPY entrypoint.sh /usr/local/bin/codex-host
RUN chmod 755 /usr/local/bin/codex-host

ENV HOME=/home/codex \
    CODEX_HOME=/home/codex/.codex \
    PATH=/home/codex/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

USER 20213:20213
WORKDIR /home/codex
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/codex-host"]
