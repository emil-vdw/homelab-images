FROM python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    # Take Debian security fixes released after the base image.
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    # The server never installs packages; remove pip and its bundled dependencies.
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip \
    && groupadd --gid 20223 mealie-mcp \
    && useradd --uid 20223 --gid 20223 --no-create-home --home-dir /nonexistent mealie-mcp

COPY mealie_mcp ./mealie_mcp

USER 20223:20223
EXPOSE 8000
ENTRYPOINT ["python", "-m", "mealie_mcp"]
