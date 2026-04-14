#!/bin/bash

# 1. 增加参数校验，避免无TAG传入导致更新混乱
if [ -z "$1" ]; then
    echo "错误：请传入要更新的TAG名称，用法：./脚本名 <TAG>"
    exit 1
fi

TAG=$1
arch=$(uname -m)
py_version=py_310
org=aisbench
image_name=ghcr.io/${org}/aisbench_benchmark:${TAG}_${arch}_${py_version}
offline_pkg_name=ais_bench_benchmark_image_${TAG}_${arch}_${py_version}.tar.gz
offline_pkg_full_path=/home/ais_bench_ci/release_images/${offline_pkg_name}

# 2. （可选但推荐）清理本地旧镜像和旧离线包，避免缓存干扰和磁盘占用
echo "开始清理本地旧资源..."
# 删除本地旧镜像（若存在）
if docker images -q ${image_name} > /dev/null 2>&1; then
    docker rmi -f ${image_name}
    echo "已删除本地旧镜像：${image_name}"
fi
# 删除本地旧离线包（若存在）
if [ -f "${offline_pkg_full_path}" ]; then
    rm -f "${offline_pkg_full_path}"
    echo "已删除本地旧离线包：${offline_pkg_full_path}"
fi

# 3. 修正docker build参数格式，确保--build-arg传递有效，保留--no-cache避免缓存导致更新不彻底
echo "开始构建新镜像（强制不使用缓存，确保更新完整）..."
docker build \
    --no-cache \
    --network host \
    --build-arg GIT_TAG=${TAG} \
    -t ${image_name} \
    .

# 构建失败则直接退出，避免后续无效操作
if [ $? -ne 0 ]; then
    echo "错误：镜像构建失败，终止后续操作"
    exit 1
fi