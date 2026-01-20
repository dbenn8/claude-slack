# Multi-distro Dockerfile for claude-slack testing
# Usage:
#   Debian: docker build --build-arg BASE_IMAGE=debian -t claude-slack-test .
#   Fedora: docker build --build-arg BASE_IMAGE=fedora -t claude-slack-test .
#
# For devcontainer: build context is parent directory (..)

ARG BASE_IMAGE=debian

# ============= Debian Base =============
FROM debian:bookworm-slim AS base-debian

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ============= Fedora Base =============
FROM fedora:40 AS base-fedora

RUN dnf install -y \
    python3 \
    python3-pip \
    git \
    curl \
    && dnf clean all

# ============= Final Stage =============
FROM base-${BASE_IMAGE} AS final

# Create non-root user matching devcontainer expectations
ARG USERNAME=developer
ARG USER_UID=1000
ARG USER_GID=$USER_UID

RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME \
    && mkdir -p /home/$USERNAME/.claude \
    && chown -R $USERNAME:$USERNAME /home/$USERNAME

# Set up working directory
WORKDIR /workspace
RUN chown -R $USERNAME:$USERNAME /workspace

USER $USERNAME

# Create virtual environment
RUN python3 -m venv /home/$USERNAME/.venv
ENV PATH="/home/$USERNAME/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/home/$USERNAME/.venv"

# Install Python dependencies (context is parent dir for devcontainer)
COPY --chown=$USERNAME:$USERNAME requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

# Copy project files
COPY --chown=$USERNAME:$USERNAME . .

# Set Python path
ENV PYTHONPATH="/workspace/core"

# Default command runs tests
CMD ["pytest", "tests/", "-v", "--tb=short"]
