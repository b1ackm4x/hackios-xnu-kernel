FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git curl build-essential clang lld llvm nasm acpica-tools \
    gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu uuid-dev device-tree-compiler \
    python3 python3-pip python3-venv python3-dev python3-setuptools \
    7zip liblz4-dev libssl-dev zlib1g-dev unzip zip make cmake ninja-build \
    gawk flex bison m4 autoconf automake libtool libxml2-dev libzstd-dev \
    libncurses-dev rsync meson pkg-config xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
