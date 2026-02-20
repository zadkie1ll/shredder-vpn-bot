#!/bin/bash

docker buildx build \
  --platform linux/amd64 \
  -t monkey-island-vpn-bot:v0.1 \
  -f Dockerfile \
  --output type=docker,dest=monkey-island-vpn-bot-amd64.tar ../..
