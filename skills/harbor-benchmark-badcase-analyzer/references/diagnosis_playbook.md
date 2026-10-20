# badcase 分析手册（阶段三）

目标：对每个 badcase 给出**具体、可核验**的「表现不正常的原因」。每条结论都必须**附证据**：
**文件 + step_id / 行号 + 原文片段**。

## 取证顺序（固定）

```
trial result.json → exception.txt → trial.log → verifier/* → artifacts/manifest.json
→ read_trajectory.py → agent 变层产物（适配器命中时）→ 结合 job 级配置
```

逐项要点：

1. **`trial/result.json`**：`exception_info`（类型/消息/堆栈）、阶段耗时
   （`agent_execution` 是否吃满 `timeout_multiplier` × 任务时限）、`agent_result` 的 token/成本
   （`cost_usd=null` 常伴 `Cannot estimate cost for model 'deepseek-v4-flash'`）。
2. **`exception.txt`**：完整堆栈，定位是**基础设施**还是**业务**异常。
3. **`trial.log`**：实际启动命令、deps bundle 选择、**glibc/镜像不匹配**等旁路告警，例如
   `Agent deps bundle was built on base image 'ubuntu:24.04' but the task environment uses
   'alexgshaw/<task>:<tag>'; binaries may be incompatible (glibc etc.)`。
4. **`verifier/*`**：`reward.txt`（权威分数）、`ctrf.json`（逐测试 passed/failed + `trace` 内嵌的**判分源码**
   → 据此还原任务**真正要求**）、`test-stdout.txt`（测试期真实报错）。
5. **`artifacts/manifest.json`**：`status:"empty"` 说明该服务未产出定向产物。
6. **`read_trajectory.py`**（见下）：核心证据源。
7. **agent 变层产物**：仅当 `agent/` 命中已登记适配器；未命中**停下问用户**。
8. **结合 job 级配置**：`disallowed_tools`（如 WebSearch 被禁）、`n_concurrent`、`timeout_multiplier`、
   agent/model/effort —— 同一现象在不同配置下解释不同（见方案 §3.3）。

## trajectories 取证（`scripts/read_trajectory.py`）

ATIF v1.7：`{schema_version, session_id, agent{name,version,model_name,extra{cwds,git_branches}}, steps[]}`；
step 含 `step_id, timestamp, source, message, reasoning_content, model_name, tool_calls[], observation{results[]}, extra.is_sidechain`。

```
python3 read_trajectory.py <trial_dir> --list            # 逐 step 一览（默认）
python3 read_trajectory.py <trial_dir> --errors          # 只看 stderr / interrupted / 无结果 的 step
python3 read_trajectory.py <trial_dir> --step N --chars 1500   # 单步细节
python3 read_trajectory.py <trial_dir> --json --out ex.json    # 结构化导出
```

- 判读线索：`reasoning_content`（agent 自述意图/困惑）、`tool_calls[].function_name`（动作分布）、
  `observation.results[].extra.tool_result_metadata.tool_use_result.{stdout,stderr,interrupted}`
  （结果状态）、`extra.is_sidechain`（子代理）、以及**最后若干 step**（是否「没验证就宣布完成」）。
- ⚠️ **禁止整读** `trajectory.json` 进上下文；脚本输出**一律截断**（`--chars` 控制）。
- 变层次级的原始流（claude-code 的 `agent/claude-code.txt`、`agent/sessions/tasks/*.json`）
  在需要「它在哪一步放弃、计划如何演变」时再读，同样**只读片段**。

## 原因分类法（8 类，每条结论归入其一）

| # | 类别 | 典型证据 |
|---|---|---|
| 1 | **环境 / 基础设施** | 镜像构建失败、glibc 不匹配、apt/dpkg 故障、代理不通（`job.log`/`trial.log`/`exception.txt`） |
| 2 | **Agent 运行时** | 超时（`AgentTimeoutError`）、取消（`CancelledError`）、API 错误、上下文溢出、成本无法估算 |
| 3 | **任务理解错误** | 读错文件、误解题意（`reasoning_content` + 早期 step + `ctrf.trace` 的判分要求） |
| 4 | **方案错误** | 路线跑偏、过度设计（中期 step 的 plan / 反复改写） |
| 5 | **实现缺陷** | 代码 bug、边界遗漏（`stderr`、`test-stdout.txt`） |
| 6 | **自检不足** | 没跑验证就宣布完成（末尾 step 无验证动作、无 `ctrf` 通过） |
| 7 | **题目 / 验证器问题** | flaky、过严、依赖不可得资源（`ctrf.trace`、多次 attempt 结果不一致） |
| 8 | **能力上限** | 明确知道该做什么但做不到（`reasoning_content` 显示理解正确却失败） |

## 输出要求

- 每个 badcase 一段：`trial → 判定（方法/置信/severity）→ 根因类别 → 证据（文件+step_id+原文片段）→ 一句话结论`。
- T3 判定的 badcase **必须**标注「低置信」并列出邻居在该题的通过/失败事实。
- 可疑通过同样要核轨迹（重点看是否「空跑测试/绕过验证」）。
- 证据不足以下结论时，**明确写「证据不足」并停下问用户**，不得编造。
