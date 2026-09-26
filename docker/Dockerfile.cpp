FROM rust:1.85-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ python3 \
    && rm -rf /var/lib/apt/lists/*
