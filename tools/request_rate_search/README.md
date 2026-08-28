# request_rate_search —— 为 ais_bench 测试任务寻优 request rate

## 简介

该工具用于对某个 ais_bench 性能测试任务寻找**最优 request rate**：

- **最优的定义**：在满足“要求1”（TTFT / TPOT 的耗时在各自门限约束内、预期成功率不低于门限）的前提下，Request Throughput 最高。由于吞吐随 request rate 单调递增（直至饱和），等价于**满足要求1的最高 request rate**。
- 工具以子进程方式执行用户提供的 shell 脚本（内含 ais_bench 基准命令），每轮在命令末尾追加 `--request-rate <rate>` 强制指定请求速率。
- 运行期间实时监控 ais_bench 落盘的性能数据（`performances/<abbr>/tmp/` 下的流式文件），一旦判定不满足“要求1”立即中断本轮（提前打断），继续试探下一个 request rate。
- 每轮打印关键日志（至少包含请求数与 Request Throughput），全部轮次汇总到一个 CSV，最后打印 CSV 路径。

## 依赖与环境

- Python3 + numpy（numpy 用于百分位计算与 sqlite 中 numpy 数组解析；可复用项目已有的 Python 环境）。
- 执行 ais_bench 的 shell 环境（bash），以及脚本内所需的虚拟环境/命令（如 `ais_bench` 可执行）。

## 快速开始

### 1. 准备 ais_bench 基准命令脚本

复制 `run_ais_bench.example.sh` 并按需修改：

```bash
cp tools/request_rate_search/run_ais_bench.example.sh my_run_ais_bench.sh
# 编辑 my_run_ais_bench.sh，将基准命令替换为你的 ais_bench 命令
```

脚本约束（重要）：

- 必须以 `"$@"` 结尾，用于透传工具追加的 `--request-rate` 等参数；
- 命令对应的**模型配置必须已包含 `request_rate` 字段**（AISBench 的 CLI 覆盖仅作用于配置中已存在的字段）；
- 相对路径基于运行工具时的工作目录解析（建议在 `benchmark/` 根目录下运行）。

### 2. 运行工具

```bash
python tools/request_rate_search/request_rate_search.py \
    --script my_run_ais_bench.sh \
    --rate-min 0.5 --rate-max 100 \
    --ttft-threshold P90:2000 --ttft-threshold P95:2500 \
    --tpot-threshold P90:50 \
    --output-dir outputs/rate_search
```

## 命令行参数

| 参数 | 类型/默认 | 说明 |
|---|---|---|
| `--script` | str，必填 | 含 ais_bench 基准命令的 shell 脚本路径（须以 `"$@"` 透传） |
| `--rate-min` / `--rate-max` | float，必填 | 寻优范围，如 0.5 / 100 |
| `--ttft-threshold` | 可重复，必填 | TTFT 门限约束对，格式 `PXX:门限ms`，如 `P90:2000`；可重复传入（P90、P95 各有门限） |
| `--tpot-threshold` | 可重复，必填 | TPOT 门限约束对，格式 `PXX:门限ms`，可重复传入 |
| `--expected-requests` | int，可选 | 预期总请求数（提前打断判定分母）；缺省自动从任务看板进度解析，解析不到回退为“已完成数”并告警 |
| `--early-abort-ratio` | float，可选 | 全局允许违反比例；缺省按各百分位推导 `(100-PXX)/100`（P90→0.1，P95→0.05，P99→0.01） |
| `--success-rate-threshold` | float，可选 | 预期成功率门限（`预期成功率=(总请求数-失败请求数)/总请求数`），如 `0.99`；低于门限即判定不满足；缺省不校验 |
| `--min-samples` | int，默认 10 | 已完成成功样本数低于该值时不提前打断 |
| `--monitor-interval` | float，默认 5.0 | 监控轮询间隔（秒） |
| `--descent-factor` | float，默认 0.5 | 算法1 等比下降因子 |
| `--tol` | float，默认 0.05 | 算法1 收敛相对容差（相对 rate-max） |
| `--max-rounds` | int，默认 20 | 最大轮次兜底 |
| `--work-dir` | str，默认 `outputs/default` | ais_bench 工作目录（用于回退定位实验目录） |
| `--output-dir` | str，默认 `outputs/rate_search` | 汇总 CSV 与轮次日志输出目录 |
| `--model-abbr` | str，可选 | 指定监控的模型 abbr；缺省自动取 `performances/` 下最新子目录 |
| `--shell` | str，默认 `bash` | 执行脚本的 shell |

## 核心逻辑说明（两处可独立改动的函数）

两个关键逻辑均封装为独立函数，位于 `request_rate_search.py`，可按需独立修改：

### “要求1” —— `requirement_1(...)`

判断性能数据是否满足门限要求，支持 TTFT 与 TPOT 各自配置**多个（百分位, 门限）约束对**（如 P90、P95 各有门限），并可选校验**预期成功率**。

- **最终判定**（全部请求完成后）：每个约束对均需满足 `PXX(样本) ≤ 门限`；若配置了 `--success-rate-threshold`，还需满足 `预期成功率 = (总请求数 - 失败请求数) / 总请求数 ≥ 门限`。
- **运行中提前判定**（请求未跑完时）：
  - 对每个约束对统计违反样本数（样本值 > 门限），若 `违反数 / 预期总请求数 > 允许比例`（缺省 `(100-PXX)/100`）即判定不满足并触发打断。已完成样本数不足 `--min-samples` 时不提前打断。
  - 失败请求数同样实时累计：一旦 `(总请求数 - 当前失败请求数) / 总请求数 < 成功率门限` 即判定不满足并触发打断（失败数不足 `--min-samples` 时也生效，失败是确定事件）。

TTFT/TPOT 计算口径与 ais_bench 汇总器一致：`ttft = tp[1] - tp[0]`，`tpot = (latency - ttft) / (output_tokens - 1)`（单位换算为 ms）。

### “算法1” —— `algorithm_1(...)`

根据历史轮次结果选择下一轮 request rate：

1. 首轮从范围上限 `rate_max` 开始；
2. 一路失败则按 `descent_factor`（默认 0.5）等比下降，直至 `rate_min`；
3. 出现通过点后，在通过点与上方最近失败点之间二分细化，区间宽度 ≤ `tol × rate_max` 时收敛；
4. 若 `rate_max` 已通过，则最优即为 `rate_max`；若一路失败到 `rate_min` 仍不通过，则范围内无可行 rate。

## 落盘结果定位（工具如何知道结果目录）

- 工具从 ais_bench 输出日志中解析关键行定位结果目录：
  - `Current exp folder: <work_dir>/<timestamp>`（实验根目录）
  - `Performance Result files located in <performances/<abbr> 目录>`（最终结果目录）
- 运行中监控 `performances/<abbr>/tmp/` 下的流式落盘数据（`tmp_*.jsonl` + sqlite db）。
- 正常完成后从最终目录读取 `<dataset>.json`（Request Throughput / Total Requests / Success Requests / Failed Requests）与 `<dataset>.csv`（TTFT/TPOT 各 PXX 列）。

## 输出说明

- `--output-dir/summary.csv`：每轮一行，列含 `round, request_rate, passed, interrupted, requests_completed, expected_total, failed_requests, expected_success_rate, request_throughput_req_s, ttft_<PXX>_ms..., tpot_<PXX>_ms..., vratio_TTFT_<PXX>..., vratio_TPOT_<PXX>..., result_dir, log_path, note`。
- `--output-dir/logs/round_<rate>.log`：每轮 ais_bench 完整输出日志。
- 工具结束时会打印汇总 CSV 的绝对路径，以及最优 request rate 及其 Request Throughput。

## 注意事项

- **预期总请求数**（提前打断的分母）获取顺序：`--expected-requests` > 任务看板进度解析 > 回退为“已完成数”（会告警）。`--num-prompts` 会直接影响总请求数，如需精确判定建议显式传入 `--expected-requests`。
- 打断轮次会在 `performances/<abbr>/tmp/` 及日志中留下部分残留数据，属正常现象，不影响后续轮次（每轮实验目录独立）。
- 默认场景为**单模型 + 单数据集**；多任务配置下工具会聚合 `performances/` 下所有子目录的流式数据。
- 同一百分位配置多个门限时，取更严（更小）的门限生效。
