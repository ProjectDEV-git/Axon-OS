# Dockerfile — Containerized ISO Build Setup for Axon OS
# Build with: docker build -t axon-builder .
# Run with: docker run --privileged -v $(pwd):/workspace -v /tmp/axon-build:/tmp/axon-build axon-builder

FROM ubuntu:24.04

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Reproducible builds: set SOURCE_DATE_EPOCH
# Override at build time with: docker build --build-arg SOURCE_DATE_EPOCH=<timestamp>
ARG SOURCE_DATE_EPOCH=1704067200
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

# Install live-build dependencies and utilities
RUN apt-get update && apt-get install -y \
    live-build \
    debootstrap \
    xorriso \
    squashfs-tools \
    rsync \
    wget \
    curl \
    ca-certificates \
    git \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /workspace

# Default entry point is running the master build script
CMD ["/workspace/build/build.sh"]
