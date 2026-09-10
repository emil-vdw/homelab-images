FROM node:24-trixie-slim@sha256:50c3b2f6988dfc307b86e5301d69611af31f4789bdf232863b07d3b02fe55ae0

ARG CODEX_VERSION=0.153.4
ARG CODEX_INSTALLER_SHA256=ba92dd27e5c06f0d3bbc58bfa4b9cfb6599cd2742fbb1f92a2765e6c07dedb5a

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       bash build-essential ca-certificates curl gh git jq less openssh-client \
       procps python3 python3-icalendar python3-pip python3-venv ripgrep tini unzip \
    && rm -rf /var/lib/apt/lists/*

# Keep the official standalone layout, including its bundled executables.
# The installer verifies release asset digests; pin the installer itself too.
RUN curl -fsSL https://chatgpt.com/codex/install.sh -o /tmp/install-codex.sh \
    && echo "${CODEX_INSTALLER_SHA256}  /tmp/install-codex.sh" | sha256sum -c - \
    && CODEX_HOME=/opt/codex CODEX_INSTALL_DIR=/usr/local/bin CODEX_NON_INTERACTIVE=1 \
       sh /tmp/install-codex.sh --release "${CODEX_VERSION}" \
    && rm /tmp/install-codex.sh \
    && codex --version

ARG KUBECTL_VERSION=1.35.8
ARG FLUX_VERSION=2.9.5
ARG HELM_VERSION=4.2.4

# Use upstream binaries for both supported architectures and verify each
# release's checksums. Keep kubectl within one minor of the cluster API.
RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in amd64|arm64) ;; *) exit 1 ;; esac; \
    mkdir /tmp/cluster-tools; cd /tmp/cluster-tools; \
    curl -fsSLO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}/kubectl"; \
    curl -fsSLO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}/kubectl.sha256"; \
    echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c -; \
    install -m 755 kubectl /usr/local/bin/kubectl; \
    curl -fsSLO "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/flux_${FLUX_VERSION}_linux_${arch}.tar.gz"; \
    curl -fsSLO "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/flux_${FLUX_VERSION}_checksums.txt"; \
    grep " flux_${FLUX_VERSION}_linux_${arch}.tar.gz\$" "flux_${FLUX_VERSION}_checksums.txt" | sha256sum -c -; \
    tar -xzf "flux_${FLUX_VERSION}_linux_${arch}.tar.gz" flux; \
    install -m 755 flux /usr/local/bin/flux; \
    curl -fsSLO "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${arch}.tar.gz"; \
    curl -fsSLO "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${arch}.tar.gz.sha256sum"; \
    sha256sum -c "helm-v${HELM_VERSION}-linux-${arch}.tar.gz.sha256sum"; \
    tar -xzf "helm-v${HELM_VERSION}-linux-${arch}.tar.gz" "linux-${arch}/helm"; \
    install -m 755 "linux-${arch}/helm" /usr/local/bin/helm; \
    cd /; rm -rf /tmp/cluster-tools

RUN groupadd --gid 20213 codex \
    && useradd --uid 20213 --gid 20213 --create-home --shell /bin/bash codex \
    && chmod 700 /home/codex

COPY productivity/*.py /opt/homelab-tools/
COPY productivity/homelab-vikunja productivity/homelab-radicale /usr/local/bin/
RUN chmod 755 /usr/local/bin/homelab-vikunja /usr/local/bin/homelab-radicale \
    && python3 -m unittest discover -s /opt/homelab-tools -v \
    && homelab-vikunja --help \
    && homelab-radicale --help

COPY entrypoint.sh /usr/local/bin/codex-host
RUN chmod 755 /usr/local/bin/codex-host

ENV HOME=/home/codex \
    CODEX_HOME=/home/codex/.codex \
    PATH=/home/codex/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

USER 20213:20213
WORKDIR /home/codex
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/codex-host"]
