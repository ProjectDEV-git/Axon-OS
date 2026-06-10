# Dockerfile — Containerized ISO Build Setup for Axon OS
# Build with: docker build -t axon-builder .
# Run with: docker run --privileged -v $(pwd):/workspace -v /tmp/axon-build:/tmp/axon-build axon-builder

FROM ubuntu:24.04

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

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
