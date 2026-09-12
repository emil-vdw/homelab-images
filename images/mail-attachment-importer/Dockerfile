FROM python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade libpcre2-8-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    # The job never installs packages; remove pip and its bundled dependencies.
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip \
    && groupadd --gid 20219 importer \
    && useradd --uid 20219 --gid 20219 --no-create-home --home-dir /nonexistent importer

COPY mail_importer ./mail_importer

USER 20219:20219
ENTRYPOINT ["python", "-m", "mail_importer"]
