# 变层：agent 相关产物的**发现协议**（不写死）

harbor 的 **固定层**（见 `result_files.md`）对所有 agent 一致；但不同 agent 会在 trial 的
`agent/` 目录下写**各自特有**的产物。这些**不能写死**，必须在使用时**动态发现**。

## 协议（`scripts/profile_job.py` 已实现）

1. 读 job `config.json` 的 `agents[].name` → 得到 agent 标识（如 `claude-code`）。
2. 枚举每个 trial 的 `agent/` 目录下**全部**相对路径（`profile_job` 输出 `agent_files`）。
3. 用**适配器注册表** `AGENT_ADAPTERS` 匹配 `agent_files`：
   - **命中** → 记录 `agent_artifacts_matched{<agent>:[{file,kind,bytes}]}`，可解析。
   - **未命中** → `agent_artifact_protocol.needs_user_confirmation = true`，输出 `unknown_agents`。
     ⚠️ **必须停下来问用户**，不得猜测其语义（属 skill 范围外行为）。

## 首批已登记适配器

### `claude-code`

| 文件 / 模式 | kind | 说明 |
|---|---|---|
| `agent/claude-code.txt` | claude-code stream-json raw log | agent 的 stream-json 原始流 |
| `agent/sessions/tasks/*.json` | agent self-managed todo list | agent 自己的待办列表（暴露其计划与放弃点） |
| `agent/sessions/*.json` | agent session transcript | 会话记录 |

**实测目录形态**（round2 / `gpt2-codegolf__oHP6GP3`）：

```
agent/
├── claude-code.txt                     # stream-json 原始流
├── trajectory.json                     # ATIF v1.7（固定层，但物理上位于 agent/）
└── sessions/
    ├── tasks/<session-uuid>/{1.json,2.json,3.json,.lock}   # 待办列表
    ├── shell-snapshots/snapshot-bash-<ts>-<id>.sh
    └── .last-cleanup
```

> `agent/trajectory.json` 虽是 ATIF（agent 无关），但物理上位于 `agent/` 下，二者都要认。
> `agent/sessions/tasks/<uuid>/N.json` 是 agent 自管待办的分页快照，可用于判断它在哪一步放弃。

## 其余 agent

**留空待补**。遇到未登记 agent（如 `codex` / `terminus-2` / `gemini-cli` 等）时：
**先问用户**该 agent 的产物语义，确认后再补进 `AGENT_ADAPTERS`。
