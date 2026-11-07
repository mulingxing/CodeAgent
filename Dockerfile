FROM python:3.12-slim AS git-builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential xz-utils zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:26c56c296b38c0695b26fa95f475f1d01704d2d38e73465ca30b0b2f5dc789d3 \
    https://www.kernel.org/pub/software/scm/git/git-2.56.0.tar.xz /tmp/git.tar.xz
RUN tar -xf /tmp/git.tar.xz -C /tmp
WORKDIR /tmp/git-2.56.0
RUN make -j2 prefix=/usr/local NO_CURL=YesPlease NO_GETTEXT=YesPlease NO_TCLTK=YesPlease \
    NO_EXPAT=YesPlease NO_PCRE2=YesPlease NO_OPENSSL=YesPlease NO_PERL=YesPlease \
    NO_RUST=YesPlease git

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --from=git-builder /tmp/git-2.56.0/git /usr/local/bin/git

# Keep third-party packages cached when application source or the page changes.
COPY pyproject.toml ./
RUN python -c 'import pathlib, tomllib; p=pathlib.Path("pyproject.toml"); deps=tomllib.loads(p.read_text())["project"]["dependencies"]; pathlib.Path("/tmp/codeagent-requirements.txt").write_text("\n".join(deps)+"\n")' \
    && pip install --no-cache-dir 'setuptools>=68' wheel -r /tmp/codeagent-requirements.txt

COPY README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY fixtures ./fixtures

RUN pip install --no-deps --no-build-isolation -e . \
    && useradd --create-home --uid 10001 codeagent \
    && mkdir -p /app/.runtime/jobs \
    && chown -R codeagent:codeagent /app

USER codeagent
EXPOSE 8000
