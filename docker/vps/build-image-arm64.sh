#!/bin/bash

docker buildx build \
  --platform linux/arm64 \
  -t monkey-island-vps-bot:v0.1 \
  -f Dockerfile \
  --output type=docker,dest=monkey-island-vps-bot-arm64.tar ../..
