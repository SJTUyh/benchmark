#!/bin/bash
IMAGES_ID=$1 # 镜像的id
NAME=$2 # 基于镜像要启动的容器的名称
# 定义要使用的普通用户（根据镜像内实际用户调整，如benchuser/1000）
USER_NAME="ais_bench_smoke"  # 或直接用UID：USER_UID=1000
WORK_DIR="/home/${USER_NAME}/smoke_ci_runners/${NAME}"
#WORK_DIR="/home/${USER_NAME}/master_code/benchmark"

# 启动容器（核心：-u 指定用户，--user也可）
docker run --name ${NAME} -it -d --net=host --ipc=host \
    --user $(id -u ${USER_NAME}):$(id -g ${USER_NAME}) \
    -v ${WORK_DIR}:${WORK_DIR} \
    -v /home/${USER_NAME}/smoke_datasets:/home/${USER_NAME}/smoke_datasets \
    -v /etc/hosts:/etc/hosts \
    ${IMAGES_ID} \
    /bin/bash
