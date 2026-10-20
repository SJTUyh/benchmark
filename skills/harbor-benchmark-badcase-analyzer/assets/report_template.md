# Harbor 基准测试 badcase 分析报告

> 本文件由 `harbor-benchmark-badcase-analyzer` skill 生成。
> 模板中的 `{{...}}` 为占位符；无内容的章节保留标题并写「无」，不要删除章节。

---

## 0. 概要

| 项 | 值 |
|---|---|
| 被测 job 目录 | `{{job_dir}}` |
| job 名 | `{{job_name}}` |
| 基准 | `{{benchmark}}` |
| Agent | `{{agent.name}}` (v{{agent.version}}) |
| 模型 | `{{agent.model}}`（provider: `{{agent.provider}}`） |
| effort | `{{agent.effort}}` |
| 被测观测均分 | `{{observed_mean_reward}}`（n 个已评分 trial） |
| trial 总数 | `{{n_trials}}`（已评分 `{{n_scored}}` / 通过 `{{n_pass}}` / 失败 `{{n_fail}}`） |
| 判定参数 | α={{alpha}} β={{beta}} min_n={{min_n}} k={{k}} p_threshold={{p_threshold}} |
| 判定状态 | **{{status}}**（OK / NEED_USER_INPUT） |
| 报告生成时间 | `{{generated_at}}` |
| 种子库快照 | `{{db_snapshot}}`（benchmark / 生成时间 / 题数） |

**一句话结论**：{{one_line_conclusion}}

---

## 1. 任务基本配置（阶段一）

来源：`profile_job.py`（只读）。固定层语义见 `references/result_files.md`，变层协议见 `references/agent_artifacts.md`。

### 1.1 job 级

| 项 | 值 |
|---|---|
| job_name | `{{config.job_name}}` |
| datasets | `{{config.datasets}}` |
| timeout_multiplier | `{{config.timeout_multiplier}}` |
| n_concurrent_trials | `{{config.n_concurrent_trials}}` |
| environment | `{{config.environment}}` |
| verifier.env keys | `{{config.verifier_env_keys}}` |
| harbor 版本 | `{{lock.harbor}}` |
| retry.exclude_exceptions | `{{lock.retry}}` |
| n_errored / n_cancelled | `{{result.stats.n_errored_trials}}` / `{{result.stats.n_cancelled_trials}}` |

agent 配置（`config.agents[]`，密钥已脱敏）：

| name | model_name | effort | disallowed_tools | deps_path | env keys |
|---|---|---|---|---|---|
| {{agent.name}} | {{agent.model_name}} | {{agent.effort}} | {{agent.disallowed_tools}} | {{agent.deps_path}} | {{agent.env_keys}} |

### 1.2 变层产物发现（不写死）

- 发现到的 agent 名：`{{agent_artifact_protocol.agent_names}}`
- 已登记适配器：`{{agent_artifact_protocol.registered_agents}}`
- 未登记（未知）agent：`{{agent_artifact_protocol.unknown_agents}}`
- 是否需用户确认：**{{agent_artifact_protocol.needs_user_confirmation}}**
- 命中产物（逐 trial 汇总）：

| agent | 产物文件模式 | 说明 | 覆盖率 |
|---|---|---|---|
| {{agent}} | {{pattern}} | {{kind}} | {{n_trials}}/{{n_trials}} |

> ⚠️ 若存在未登记 agent，**不得猜测**其产物语义；按 §5 停下问用户。

### 1.3 解读时必须考虑的配置差异

- 与同名/相近 job 的差异（如 `disallowed_tools`、`n_concurrent`、`timeout_multiplier`）：{{config_diff_note}}
- 注意 **job_name 非唯一键**（round1/round2 同名）；以目录 + job id 为准。

---

## 2. badcase 清单（阶段二）

判定算法见 `references/detection_algorithm.md`；库与降级规则见 `references/score_db.md`。

- 硬信号（直接定性）：`exception_type` 非空、`reward` 缺失。
- 软信号（仅对 `reward<1` 计权）：无 ctrf / 无 agent 产物 / 成本为 null；
  `hard_multiplier = min(2.0, 1 + 0.25×n_soft)`。
- 统计异常：T1 精确（n≥3）→ T2 IRT → **T3 邻居投票（低置信）**；
  `is_badcase = 硬信号 or (reward<1 且 p̂ ≥ threshold)`；`severity = (1 − p̂) × hard_multiplier`。

### 2.1 badcase 汇总（按 severity 降序）

| # | trial | task | reward | 判定方法 | 置信 | p̂ | severity | 信号 |
|---|---|---|---|---|---|---|---|---|
| 1 | {{trial}} | {{task}} | {{reward}} | {{method}} | {{confidence}} | {{p_hat}} | {{severity}} | {{signals}} |

统计：badcase 共 **{{counts.badcases}}** 个（按方法 {{counts.by_method}}）。

### 2.2 可疑通过（reward≥1 但验证可疑）

| trial | task | reward | 原因 |
|---|---|---|---|
| {{trial}} | {{task}} | {{reward}} | {{reasons}} |

### 2.3 无法评分（NEED_USER_INPUT）

| trial | task | reward | 原因 |
|---|---|---|---|
| {{trial}} | {{task}} | {{reward}} | {{reason}} |

> 存在本节内容时，**status = NEED_USER_INPUT**：这些失败 trial 无法被 T1/T2/T3 覆盖，
> 属 skill 范围外，必须先与用户确认再解读。

---

## 3. 逐个 badcase 的原因分析（阶段三）

每个 badcase 一节，格式固定；**每条结论必须附证据：文件 + step_id/行号 + 原文片段**。

### 3.{{index}}. `{{task}}__{{suffix}}`

| 项 | 值 |
|---|---|
| trial 目录 | `{{trial}}` |
| 判定 | method={{method}} / confidence={{confidence}} / p̂={{p_hat}} / severity={{severity}} |
| 硬信号 | {{hard_signals}} |
| 软信号 | {{soft_signals}}（multiplier={{severity_multiplier}}） |
| 根因类别 | {{category}}（见 `references/diagnosis_playbook.md` 8 类） |

**证据链**（取证顺序：trial result.json → exception.txt → trial.log → verifier/* → artifacts/manifest.json → trajectory）：

1. `{{file}}` @ {{step_id/line}}：`{{quote}}`
2. `{{file}}` @ {{step_id/line}}：`{{quote}}`

**轨迹关键点**：

- 最后若干 step：{{last_steps}}
- 错误/中断 step：{{error_steps}}
- 自述与计划：{{reasoning_highlights}}

**结论**：{{conclusion}}

> T3（低置信）判定时，**必须**在此列出邻居模型在该题的通过/失败事实：

| 邻居 config | θ | 距离 | 该题 n | 该题 k | 该题 p̃ |
|---|---|---|---|---|---|
| {{config_key}} | {{theta}} | {{dist}} | {{n}} | {{k}} | {{p_tilde}} |

---

## 4. 附录

### 4.1 数据来源与可溯源

| 项 | 值 |
|---|---|
| 榜单来源 | {{leaderboard.sources}}（含 URL 与快照时间） |
| 种子库 | 随 skill 打包的 `assets/harbor_scores.db`（{{db_snapshot}}） |
| 库覆盖 | configs={{n_configs}} / tasks={{n_tasks}}（n_obs<3 的题：{{sparse_tasks}}） |
| 被测数据 | `{{job_dir}}`（**只读**，未做任何修改） |

### 4.2 置信度说明

| 档位 | 触发条件 | 含义 |
|---|---|---|
| hard | 硬信号 | 与题目难度无关的执行异常 |
| T1/exact | 库中该 (benchmark, task, agent, model) n≥3 | 直接实测通过率 |
| T2/irt | 目标 config 有 θ | `σ(θ − b_task)` |
| T3/low | 目标 config 无 θ（如 deepseek-v4-flash） | 邻居投票，**低置信**；须并列邻居事实 |

### 4.3 复现命令

```bash
python3 scripts/profile_job.py  <job_dir> --out profile.json
python3 scripts/detect_badcases.py <job_dir> --out detect.json
python3 scripts/read_trajectory.py <trial_dir> --errors
```

### 4.4 范围外行为声明

本次分析中**未**执行任何需审批的操作（联网 / 安装 / 下载 / 修改结果文件 / 执行 case 内代码）；
如需刷新榜单或补数据，须先经用户同意（见 SKILL.md §审批边界）。

---

## 5. 待用户确认事项（如有）

- {{pending_questions}}
