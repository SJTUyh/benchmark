#!/bin/bash

TAG=$1
USER_NAME="ais_bench_smoke"
docker build --build-arg APP_USER=${USER_NAME} \
  --build-arg APP_UID=$(id -u $USER_NAME) \
  --build-arg APP_GID=$(id -g $USER_NAME) \
  -t aisbench_benchmark:${TAG}_CI -f ./Dockerfile.py312.ubuntu24.04.ci .

echo "build success"
