# 固定层：harbor 标准产物语义

harbor 的 job 结果目录对所有 agent / 基准**结构一致**。阶段一由 `scripts/profile_job.py`
统一解析，本文件固化每个文件的**含义与可提取内容**（`profile_job.py` 的
`JOB_FIXED_FILES` / `TRIAL_FIXED_FILES` 常量与此表一一对应）。

目录形态：`<job_dir>/<task>__<hash>/…`（`<task>__<hash>` 即一次 trial 的结果目录）。

## job 级

| 文件 | 含义 | 可提取内容 |
|---|---|---|
| `config.json` | job 配置 | `job_name`、`agents[]`（`name`/`model_name`/`kwargs`/`env`）、`datasets[]`（`path`）、`timeout_multiplier`、`n_concurrent_trials`、`verifier.env` |
| `lock.json` | 解析后的锁定信息 | `harbor{version,git_commit_hash}`、`retry.exclude_exceptions`、`n_concurrent_trials`、逐 trial 的 `task{name,digest,source,path}`（文件可达数百 KB，**分段读**） |
| `result.json` | job 汇总结果 | `n_total_trials`、`stats.n_completed/n_errored/n_cancelled_trials`、`stats.evals[*]`：`metrics[].mean`、`reward_stats.reward{"1.0":[…trial_name],"0.0":[…]}`、`exception_stats{<ExcType>:[…]}`、token/成本 |
| `job.log` | job 运行日志 | 镜像构建 / 离线依赖安装异常（`Installing offline agent deps …`、`Selected offline deps bundle …`） |

> ⚠️ `job_name` **不是唯一键**（round1 与 round2 同名 `2026-09-03__17-34-32`）。跨 job 索引用
> **job id + 结果目录路径**。

## trial 级

| 文件 | 含义 | 可提取内容 |
|---|---|---|
| `config.json` | trial 配置 | task、agent(name/model_name/env/deps_path/mcp_servers)、environment、verifier、timeout_multiplier、job_id |
| `result.json` | trial 结果 | `verifier_result.rewards.reward`、`exception_info{exception_type,exception_message,exception_traceback}`、`agent_info`、`agent_result{n_input_tokens,n_cache_tokens,n_output_tokens,cost_usd}`、`started_at/finished_at`、阶段耗时（`environment_setup/agent_setup/agent_execution/verifier`）、`trial_uri` |
| `trial.log` | trial 日志 | 实际启动命令、deps bundle 选择、运行时旁路告警（**含 glibc 不匹配告警**） |
| `exception.txt` | 异常全文 | 完整堆栈（`exception_info` 的人类可读版） |
| `verifier/ctrf.json` | 测试报告（CTRF） | `results.tool.name`（如 pytest）、`results.summary{tests,passed,failed,skipped}`、`results.tests[]{name,status,duration,trace,message}`；★ `trace` 常**内嵌测试源码**（判分要求据此还原） |
| `verifier/reward.txt` | 分数权威文件 | 纯数字（如 `1`） |
| `verifier/test-stdout.txt` | 测试期输出 | 测试真实报错 |
| `artifacts/manifest.json` | 产物收集状态 | `[{source,destination,type,status,service}]`；`status:"empty"` 表示该服务无产物 |
| `agent/trajectory.json` | **ATIF v1.7** 轨迹 | agent 无关；逐 step 的 message / reasoning / tool_calls / observation（见阶段三） |

## ⚠️ 缺文件是常态，不是信号

实测（round2，89 trials）：

- 有 trial 目录**只有 `lock.json` + `trial.log`**（无 `result.json`/`config.json`）→ reward 只能由
  **job 级 `result.json` 的 `reward_stats` 回退**（`profile_job.py` 已实现：
  `reward_source = "job.result.json"`）。
- 有 trial **既不在 `reward_stats` 的 1.0 也不在 0.0 名单**、却出现在 `exception_stats`
  （round2 `schemelike-metacircular-eval__7Tt4LHQ` 属 `CancelledError`）→ 这是 **reward 缺失**，属硬信号。
- 大量**通过**的 trial **没有 `verifier/`、也没有 `agent/` 目录**（如 `build-cython-ext__Becq34h`、
  `bn-fit-modify__DKKvavB` reward=1.0）。⇒ **「无 ctrf / 无 agent 产物」不能作独立硬信号**，
  只能作失败 trial 的加权软信号（见 `detection_algorithm.md`）。

## 读取建议

- `lock.json` 大 → 用 offset/limit 分段读。
- `trajectory.json` 大 → 一律用 `scripts/read_trajectory.py`，**禁止整读进上下文**。
- 遇到结构与本表不符且非上述「缺文件」情形 → 停下问用户，不要猜。
