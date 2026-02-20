#!/bin/bash

docker buildx build \
  --platform linux/amd64 \
  -t monkey-island-vps-bot:v0.1 \
  -f Dockerfile \
  --output type=docker,dest=monkey-island-vps-bot-amd64.tar ../..
