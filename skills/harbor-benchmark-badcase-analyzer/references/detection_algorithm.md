# 判定算法（固化于 `scripts/detect_badcases.py`）

输入：一个 job 结果目录。输出：`badcases[]`（按 `severity` 降序）、`suspicious_passes[]`、
`unscored_failures[]`、`status`。脚本**只读**，不写 job 目录内任何文件。

记目标配置 `T = (agent, model, effort)`（取 job `config.json` 的 `agents[0]`），
`config_key = <agent>|<model>|<effort>`。

## A. 执行异常 —— 硬信号（无论题目多难都算异常）

| 硬信号 | 判据 |
|---|---|
| 异常 | `exception_info` 非空 → `exception:<type>`（`AgentTimeoutError` / `CancelledError` …） |
| 缺分 | `reward` 缺失（trial 级与 job 级回退后仍为空）→ `reward-missing` |

命中任一 → `is_badcase=True`、`method="hard"`、`confidence="hard"`、`p_hat=0`。

### ⚠️ 对原始方案的修正（本机实测驱动）

原方案把「verifier 无 `ctrf.json`」「agent 零 tool call」「成本为 null」也列为**硬信号**。
**实测推翻**：round2 中大量**通过**的 trial 同样没有 `verifier/` 与 `agent/`
（如 `build-cython-ext__Becq34h`、`bn-fit-modify__DKKvavB` reward=1.0 却无 `agent/`），
故它们**不能**独立定性。已改为**软信号**，**仅对 `reward<1` 的失败 trial** 计权：

```
soft ∈ {no-ctrf, no-agent-output(agent/ 为空), cost-null}
hard_multiplier = min(2.0, 1 + 0.25 × |soft|)
```

## B. 统计异常 —— 失败但「本不该失败」

仅当 `reward<1` **且无硬信号**时，按优先级尝试，取第一个可用者：

1. **T1 精确**：库中 `(benchmark, task, T)` 的实测 `n,k`，`p̂=(k+α)/(n+α+β)`（α=β=1），需 **n≥min_n(默认3)**。
2. **T2 IRT**：目标 config 有 θ 时 `p̂ = σ(θ_config − b_task)`。
   （`b_task` 取自 `task_difficulty`；config 级 θ，**不拆** θ_model/δ_agent。）
3. **T3 邻居投票（本项目主路径）**：目标 `claude-code|deepseek-v4-flash|max` **不在库、无 θ**，走此路：

   ```
   锚分 S        = 被测 job 自身观测均分（profile 里 scored trials 的 reward 均值）
   θ_anchor      = 在库中 (mean_reward → θ) 曲线上线性插值(端点处线性外推) 得到
   邻居          = 库中 |θ_i − θ_anchor| 最小的 k 个 config（k 默认 3）
   权重           w_i = 1 / (1e-3 + |θ_i − θ_anchor|)
   逐题          p̃_i = 邻居 i 在该题的 Beta 收缩通过率
   投票          p̂ = Σ w_i·p̃_i / Σ w_i
   置信档        T3/low（**必须**在报告中标「低置信」）
   ```

   `detect_badcases.py` 会在 `evidence.neighbors` 里**列出邻居逐题 n/k 事实**作为解释性证据。

判定与排序：

```
is_badcase = has_hard_signal  or  (reward<1  and  p̂ ≥ p_threshold)   # p_threshold 默认 0.5
severity   = (1 − p̂) × hard_multiplier                                # 硬信号行取 p̂=0
```

`badcases` 按 `severity` 降序输出。

## C. 反向 —— 可疑通过

`reward ≥ 1` 但出现以下任一 → 记入 `suspicious_passes`（**不**计入 badcase，交阶段三核轨迹）：

- 无 `verifier/ctrf.json`；
- `ctrf.results.summary.tests == 0`（无测试）；
- `ctrf` 有 `failed` 用例；
- 存在 `exception_info`。

## D. 兜底 —— `NEED_USER_INPUT`

某失败 trial **无法**被 T1/T2/T3 任一路径评分（无锚点，或邻居 < 3）→ 进 `unscored_failures`，
并把整次运行的 `status` 置为 `NEED_USER_INPUT`。此时**属 skill 范围外，必须停下问用户**，
不得强行给出结论。

## 关键先验（避免误报）

- `make-doom-for-mips`(b=3.885)、`filter-js-from-html`(b=3.844) 是**极难题**（最强模型通过率≈5%）：
  它们的失败**大概率不是统计异常**，T3 邻居也多会失败 → `p̂` 低 → **不判为 badcase**。
  实测 round2 的 `filter-js-from-html__gZUSu8M`（reward=0、无硬信号）`p̂≈0.18`，**正确地未被判为 badcase**。
  而 `make-doom-for-mips__oQTDsbJ` 因 `AgentTimeoutError` 落硬信号，两者路径不同、均合理。

## 参数

`--k`(3) `--alpha`(1) `--beta`(1) `--min-n`(3) `--p-threshold`(0.5) `--benchmark` `--db`。
默认值先采用，后续可调（见方案 §0）。
