# 榜单：来源 / 快照 / 刷新协议

榜单用于：库中缺目标 config 或数据不足时，判断某个 LLM agent 的**整体能力档位**，
为 T3 邻居锚定提供外部参照。**不替代**库内逐题数据。

## 来源（多选，已确认）

| 来源 | 入口 | 状态 |
|---|---|---|
| **Terminal-Bench 官方榜**（hub） | <https://hub.harborframework.com/datasets/terminal-bench/terminal-bench-2-1?tab=leaderboard&leaderboard=main> | ✅ 已入库（22 行） |
| 第二独立来源 tbench.ai | <https://www.tbench.ai/leaderboard/terminal-bench/2.1> | 行链接指向 hub `rows/<row_id>`，可交叉验证 |
| **Artificial Analysis** | <https://artificialanalysis.ai/> | ⏳ **尚未抓取**：需联网，用前须经用户授权并现场快照 |

## 快照规则

- 每次抓取都写入 `<skill>/raw/hub/leaderboard.json` + 种子库（`assets/harbor_scores.db`）的 `leaderboard` 表，
  记录 **URL + `snapshot_at` 时间戳**。
- 榜单是**实时数据**；本文件只保存**最近一次快照的摘要**，完整数据以 DB / raw 为准。
- **抓取失败即显式降级并标注**，**绝不静默复用旧值**。

## 已入库快照摘要（Terminal-Bench 2.1 main，hub）

22 行落在 **12 个 job** 上，合计 9,790 trials = 22 × 445 = 22 × 89 题 × 5 attempt。
`job_id → (agent, model, effort)` 的完整映射见方案文档 §2.2。要点：

- 榜首 `Codex | GPT-6 Astra | high`（0.8742）；榜尾 `Claude Code | GLM-5.1 | max`（0.5932）。
- `c8fcaaeb…` 是 mega-job（2,225 trials，含 5 个 (agent,model) 配置）；`17d1a7f6…` 含 5 个 effort。
- 全量入库只需 **12 次 `job trials`** 调用（每次 `--limit 5000` 一页取全）。
- 关键前提事实：`harbor hub job list` **枚举不到他人 job**；真实 job_id 必须走
  `leaderboard show → row trial list → trial show` 三步链取得。
  GitHub `harbor-framework/terminal-bench-2-1/.../submissions/*.json` 的 `source_jobs`
  是 **v5 派生 id，不可访问**，勿用。

## 刷新协议

1. **先经用户授权**（联网属范围外行为）。
2. 在 WSL + 代理下运行随 skill 打包的 `<skill>/tools/harbor_score_db.py ingest-all`（会同时刷新榜单与逐题数据）。
3. 刷新后必须 `fit` 重估 θ/b（榜单与难度会变）。
4. 将新快照的时间戳与行数回写本文件；若抓取失败，标注失败原因并**保留**旧快照但**明示已过期**。
