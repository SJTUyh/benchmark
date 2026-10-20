# 种子库：schema / 构建 / 检索 / 刷新

判定 badcase 的**最重要依据**是「agent + LLM 在各基准上每题的历史实测表现」。
本库（随 skill 打包于 `assets/harbor_scores.db`）由随 skill 打包的 `tools/harbor_score_db.py` 从 **hub 官方榜单及其关联 job 的逐题数据**构建。

> ⚠️ **种子库绝不含本地被测 job**。`some_harbor_results/` 下的 4 个 job 是**被测对象**，
> 全程**只读、永不入库**（否则会自我污染）。

## schema（实测）

```
runs(run_id PK, source, job_id, job_name, benchmark, dataset_ref, agent_name, agent_version,
     model_name, model_provider, effort, config_key, n_trials, mean_reward, fetched_at, url)
trials(run_id, trial_id, trial_name, task_name, reward, status, exception_type, is_scored, attempt,
       cost_usd, input_tokens, output_tokens, cache_tokens, duration_sec, started_at, finished_at,
       PK(run_id, trial_id))
irt_params(benchmark, config_key, theta, delta_agent, n_trials, fitted_at, PK(benchmark, config_key))
task_difficulty(benchmark, task_name, b, se, n_obs, fitted_at, PK(benchmark, task_name))
leaderboard(source, benchmark, config_key, agent, model, effort, score, rank, n_trials, row_id,
            snapshot_at, url, PK(source, benchmark, config_key))
INDEX idx_trials_task(run_id, task_name);  INDEX idx_runs_cfg(benchmark, config_key);
```

约定：

- `run_id = <job_id>::<config_key>`；**`config_key = <agent>|<model>|<effort>`**。
- `trials.task_name` 已归一化（去掉 `terminal-bench/` 前缀，与本地 trial 目录名一致）。
- `leaderboard.score` 已由榜上 0–100 的 accuracy 归一为 **0–1**。
- hub 源**无 ctrf 明细**（`evals` 为空）→ 不落 `n_tests/n_passed/n_failed`；ctrf 明细在阶段三从**本地** trial 文件取。
- `run_id + trial_id` 主键 upsert → 可重放、可增量刷新。

## 构建 / 刷新（`<skill>/tools/harbor_score_db.py`）

> 全部为**联网只读**操作，必须在 **WSL + 代理**下运行，且**先经用户授权**。
> 命令行：`wsl -e bash -lc 'source /mnt/d/wsl_wkp/proxy_set.sh; python3 <工具> <子命令>'`
> ⚠️ 外层**单引号**；**禁止内联 python**（引号会被 PowerShell 破坏）。

| 子命令 | 作用 |
|---|---|
| `ingest-leaderboard [--ref 默认 terminal-bench/terminal-bench-2-1/main]` | 拉榜单 → 逐行 `row trial list` → `trial show` 取 `job_id`；落 `<skill>/raw/hub/leaderboard.json` + `jobmap.json`，写 `leaderboard` 表 |
| `ingest-job <JOB_ID>...` | `job trials --limit 5000` 取全量，落 `<skill>/raw/hub/<job_id>.trials.json`，upsert `runs`/`trials` |
| `ingest-all` | `ingest-leaderboard` 后自动对映射出的**全部** job 各跑一次 |
| `fit` | (config_key)×task 矩阵做 **Rasch/1PL JMLE**（每 sweep 后 Σθ=0 中心化，θ/b 夹 ±8），写 `irt_params`（θ、δ_agent）与 `task_difficulty`（b、se） |
| `status` | 覆盖率自检：逐 config 的 n/mean/θ/δ；题目 `n_obs` 分档 |

## 检索（`scripts/query_scores.py`，纯 stdlib，**不联网**）

DB 定位顺序：`--db` > `$HARBOR_SCORE_DB` > skill 内置 `assets/harbor_scores.db` > 向上查找 `data/harbor_scores.db`（旧布局兜底）。

| 子命令 | 作用 |
|---|---|
| `exact --task T [--agent A --model M --effort E]` | T1：Beta-Binomial 收缩通过率 `p̂=(k+α)/(n+α+β)`（α=β=1，需 n≥3） |
| `difficulty [--task T]` | 题目难度 b / se / n_obs（降序） |
| `theta [--config-key K] [--agent/--model/--effort]` | config 的 θ / δ_agent / mean_reward |
| `leaderboard [--source S]` | 榜单快照行 |
| `neighbors --anchor-score S --k K [--task T]` | T3：锚定 θ 后取 k 个最近邻居加权投票（见 `detection_algorithm.md`） |
| `snapshot` | 库总体覆盖 |

`query_scores` 的**函数可被 import**（`detect_badcases.py` 即如此复用）。

## 实测内容（本机）

- benchmark：`terminal-bench-2-1`（当前仅此一个）。
- `runs=23`（22 config + `codex|gpt-5.6-luna|max` 跨 2 job）、`trials=10,828`、
  `irt_params=22`、`task_difficulty=89`、`leaderboard=22`。
- **全部 89 题 `n_obs≥5`**（无稀薄题）→ T1 阈值 n≥3 在题目维度普遍满足。
- θ 与 mean_reward **单调一致**（`codex|gpt-6-astra|high` θ=0.97 居首、`claude-code|glm-5.1|max` θ=−1.37 垫底）。
- **最难题**：`make-doom-for-mips` b=3.885(se .587, n=124)、`filter-js-from-html` b=3.844(se .587, n=118)；
  其后 `dna-insert` 1.020、`train-fasttext` 0.941、`video-processing` 0.845、`raman-fitting` 0.822、
  `extract-moves-from-video` 0.821。
- **⚠️ T2 局限**：`θ ≈ θ_model + δ_agent` 是**加性近似**，交叉项残差大
  （`terminus-2 vs gemini-cli` 在 gemini-3-pro 上 δ 差 0.59、在 gemini-3.1-pro 上仅 −0.01）。
  ⇒ **T2 一律用 config 级 θ，不拆 θ_model/δ_agent**；`delta_agent` 仅作参考展示。
- **目标配置 `claude-code|deepseek-v4-flash|max` 不在库中**（无 θ）→ 只能走 **T3 邻居投票（低置信）**。

## 降级规则

库中查不到目标 config、或某题 `n<3`、或邻居<3 → **不猜**：按 `detection_algorithm.md`
降级到 T3、或标 `NEED_USER_INPUT` 并**停下问用户**。
