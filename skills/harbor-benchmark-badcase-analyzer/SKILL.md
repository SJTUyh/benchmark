---
name: harbor-benchmark-badcase-analyzer
description: >-
  分析 harbor 框架基准测试（如 terminal-bench-2-1）单次 job 结果，找出「任务表现不正常」的
  badcase 并给出每个 badcase 的具体原因。三阶段：① 归一化 job/case 配置 → ② 用种子评分库
  （IRT + 邻居投票）识别 badcase → ③ 结合 trajectory 取证给出根因。当用户给出一个 harbor job
  结果目录（含 `result.json` / `config.json` / `<task>__<id>/`）要求分析测试结果、找异常 case、
  定位失败原因，或要求分析 harbor/terminal-bench 基准表现时使用。
---

# Harbor 基准测试 badcase 分析

输入一个 harbor job 结果目录（如 `some_harbor_results/2026-09-03__17-34-32-round2`），
输出该次测试中「表现不正常」的 case 清单 + 每个 case 的具体原因，落盘为 Markdown 报告。

## 核心约束（最重要）

**分析过程中任何「本 skill 规定范围外」的行为，都必须先经用户同意。** 见 §审批边界。
默认一律**不执行**需审批的操作。

**所有被测数据只读**：`some_harbor_results/` 等结果目录永不写入、永不修改。
本 skill 的脚本只读结果目录，报告写到技能输出目录（或用户指定处）。

## 三阶段工作流

```
阶段一 了解任务基本配置  ── profile_job.py      ──▶ profile JSON（固定层 + 变层协议）
阶段二 识别 badcase      ── detect_badcases.py  ──▶ badcase 清单（硬信号 + T1/T2/T3）
阶段三 分析 badcase      ── read_trajectory.py  ──▶ 逐 case 根因（附证据）
                                                   ──▶ Markdown 报告（assets/report_template.md）
```

对话里只给结论摘要，**完整报告落盘为文件**。

### 阶段一：了解任务基本配置

```bash
python3 scripts/profile_job.py <job_dir> --summary
python3 scripts/profile_job.py <job_dir> --json --out profile.json
```

- **固定层**：harbor 标准产物（job/trial 的 `config.json`、`result.json`、`lock.json`、`job.log`、
  `exception.txt`、`verifier/*`、`artifacts/manifest.json`、`agent/trajectory.json`）。
  语义见 `references/result_files.md`。
- **变层**：agent 相关产物**不写死**。流程：读 `agents[].name` → 枚举 trial 的 `agent/` →
  命中已登记适配器才解析 → **未命中必须停下问用户**，不得猜测。协议见 `references/agent_artifacts.md`。
  首批只登记 `claude-code`（`agent/claude-code.txt`、`agent/sessions/tasks/*.json`）。
- **解读差异**：`disallowed_tools`、`n_concurrent`、`timeout_multiplier` 会让同一现象含义不同；
  `job_name` **不是唯一键**（round1/round2 同名），以目录 + job id 为准。

### 阶段二：识别 badcase

```bash
python3 scripts/detect_badcases.py <job_dir> --out detect.json
python3 scripts/query_scores.py neighbors --anchor-score <job 观测均分> --k 3 --task <task>
```

- **硬信号**（直接定性，与题目难度无关）：`exception_type` 非空、`reward` 缺失。
- **软信号**（仅对 `reward<1` 计权）：无 ctrf / 无 agent 产物 / 成本 null；
  `hard_multiplier = min(2.0, 1 + 0.25×n_soft)`。
  （注：计划曾把这些列为硬信号，实测大量**通过** trial 也无 `verifier/`、`agent/`，故降级为软信号。）
- **统计异常**（失败但不该失败），按优先级：
  1. **T1 精确**：库中该 `(benchmark, task, agent, model)` 的 Beta-Binomial 收缩通过率，需 n≥3。
  2. **T2 IRT**：`p̂ = σ(θ_config − b_task)`（一律用 config 级 θ，不拆 θ_model/δ_agent）。
  3. **T3 邻居投票（低置信）**：目标 config 无 θ（如 `deepseek-v4-flash` 不在榜）→ 以 job 自身
     观测均分锚定 θ，取最近的 k≥3 个 config 按 `|θ 差|` 衰减加权投票。**必须标注低置信**并列出
     邻居在该题的通过/失败事实。
- `is_badcase = 硬信号 or (reward<1 且 p̂ ≥ p_threshold)`；`severity = (1 − p̂) × hard_multiplier`。
- **反向可疑通过**：reward≥1 但无 ctrf / ctrf 零测试 / ctrf 有 failed / 带 exception。
- 无法被 T1/T2/T3 评分的失败 trial → `status=NEED_USER_INPUT`，**停下问用户**。

算法细节见 `references/detection_algorithm.md`；库 schema、ingest 协议、降级规则见
`references/score_db.md`；榜单来源与刷新协议见 `references/leaderboard.md`。

### 阶段三：分析 badcase

```bash
python3 scripts/read_trajectory.py <trial_dir> --list
python3 scripts/read_trajectory.py <trial_dir> --errors
python3 scripts/read_trajectory.py <trial_dir> --step 12 --chars 1500
```

取证顺序（固定）：

```
trial result.json → exception.txt → trial.log → verifier/* → artifacts/manifest.json
→ read_trajectory.py → agent 变层产物（适配器命中时）→ 结合 job 级配置
```

原因归入 8 类之一：环境/基础设施、Agent 运行时、任务理解错误、方案错误、实现缺陷、
自检不足、题目/验证器问题、能力上限。**每条结论附证据：文件 + step_id/行号 + 原文片段**。
T3 判定必须标「低置信」并列邻居事实。证据不足时**写「证据不足」并停下问用户**，不得编造。
详见 `references/diagnosis_playbook.md`。

## 脚本清单（纯 stdlib，只读结果目录）

| 脚本 | 阶段 | 用法 |
|---|---|---|
| `scripts/profile_job.py` | 一 | `<job_dir> [--out FILE] [--json] [--summary]` |
| `scripts/query_scores.py` | 二 | `--db <db> --benchmark <b> {exact,difficulty,theta,leaderboard,neighbors,snapshot} ...` |
| `scripts/detect_badcases.py` | 二 | `<job_dir> [--benchmark --db --k --alpha --beta --min-n --p-threshold --out --json]` |
| `scripts/read_trajectory.py` | 三 | `<trajectory.json\|trial_dir> [--list --step N --errors --chars N --json --out]` |

**数据库定位**：`--db` > `$HARBOR_SCORE_DB` > **本 skill 内置** `assets/harbor_scores.db`
> 从自身目录与 CWD 向上最多 10 层查找 `data/harbor_scores.db`（旧仓库布局的兜底）。
库随 skill 打包，**skill 目录整体复制到任何位置都能独立运行**。

**库的构建与刷新**（随 skill 打包的 `tools/harbor_score_db.py`，**不在本 skill 的分析流程内**，只在用户授权后运行）：
`ingest-leaderboard` → `ingest-job <ID>...` → `fit` → `status`。需 WSL + 代理 + harbor CLI。
构建/刷新直接写入本 skill 的 `assets/harbor_scores.db`（单一位点，无副本同步问题）。

## 审批边界

**免审批**：只读结果目录；运行本 skill 的 4 个脚本；读内置 `assets/harbor_scores.db` 与 `references/` 快照；
写报告到指定输出目录。

**必须先经用户同意**（默认一律不执行）：任何联网（刷新榜单、查 Artificial Analysis、调 hub CLI）；
`uv tool install harbor`；`harbor auth login` / 使用 API key；`harbor hub trial download`；
读取 WSL 内本机其他路径（如 `/home/...`）；修改或删除任何结果文件；重跑 harbor / docker；
执行 case 内的任何代码；把报告写到结果目录之外。

遇到范围外情形（未登记 agent、邻居<3、无锚点、证据不足、需要联网补数）→ **停下问用户**，
不要自行决定。

## 排错与复用要点

- **运行环境**：harbor CLI 需在 **WSL** 中配代理运行：`source /mnt/d/wsl_wkp/proxy_set.sh`
  （`http_proxy/https_proxy/all_proxy=http://127.0.0.1:8899`）。
- **PowerShell 引号陷阱**：从 PowerShell 调 `wsl -e bash -lc '...'` 时**外层用单引号**；
  **禁止内联 python**（引号会被剥掉/破坏），一律 `python3 <脚本文件>`。
  `python -c '...'` 在 PowerShell 下同样会剥掉内层双引号 → 写临时脚本文件再执行。
- **`trajectory.json` 可达数百 KB，禁止整读**；`read_trajectory.py` 输出一律截断（`--chars` 控制）。
- **缺文件是常态，不是信号**：许多通过 trial 只有 `lock.json` + `trial.log`；
  也有 trial 无自己的 `result.json` → reward/exception 以 job 级 `result.json` 为权威回退源。
- **长任务不要管道到 `tail`**（会缓冲，看不到进行中的进度）。
- harbor CLI 在 WSL 下的 localhost 中文告警乱码，无害。
- 报告模板：`assets/report_template.md`。

## 输出交付

1. 完整 Markdown 报告（按 `assets/report_template.md`）落盘到技能输出目录；
2. 对话中只给摘要：job 概况、badcase 数量与 top 条目、可疑通过/需确认项、报告文件链接。
