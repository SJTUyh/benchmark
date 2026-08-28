#!/bin/bash
# 示例：ais_bench 基准命令脚本。
# request_rate_search 工具会在该命令后追加 --request-rate <rate>，因此必须以 "$@" 结尾透传。
#
# 使用约束：
#   1) "$@" 用于透传工具追加的 --request-rate 等参数，不可删除；
#   2) 命令对应的模型配置必须已包含 request_rate 字段
#      （CLI 覆盖仅作用于配置中已存在的字段）；
#   3) 相对路径基于运行 request_rate_search 时的工作目录解析
#      （建议在 benchmark/ 根目录下运行工具）。
#
# 若环境需要，可在脚本开头激活虚拟环境，例如：
#   source /path/to/venv/bin/activate
ais_bench ais_bench/configs/performance_benchmark/performance_synthetic.py --mode perf "$@"
