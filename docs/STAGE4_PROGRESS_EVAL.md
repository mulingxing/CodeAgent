# 阶段 4：进度、评测和展示

> 本文保留阶段 4 当时的设计与验证记录。当前版本已支持本地 Python Git 仓库修复，功能和运行方式以根目录 [README](../README.md) 为准。

## 任务进度

任务状态仍由 `tasks.status` 决定；`tasks.phase` 表示最近阶段。Worker 将“领取任务、确认基线失败、Agent 修改、固定测试、完成或失败”等阶段写入 `task_events`。事件不保存 API Key、模型原始消息或任意命令输出。

`GET /api/v1/tasks/{id}/events` 使用 SSE 输出进度事件。事件带递增 ID；客户端可用 `Last-Event-ID` 请求头或 `since` 参数续读。连接关闭后数据库事件仍可查询。`follow=false` 可读取当前事件快照。实现采用每秒查询数据库，适合本地演示；大量并发连接时应改为消息推送与连接管理。

## 固定样例评测

`POST /api/v1/evaluations` 创建一轮包含 12 个不同 Bug 的任务，写入数据库 Outbox。Beat 最多约 15 秒后投递任务；同一 Worker 和 Sandbox 执行普通任务与评测任务。每个样例都有修复前失败的固定测试，测试与 Agent 输出都在受控工作区中处理。

| 指标 | 定义 |
| --- | --- |
| 任务成功率 | `succeeded / 12` |
| Agent 修改率 | 出现 `agent_modified` 事件的不同任务数 `/ 12` |
| 测试通过率 | 出现 `tests_passed` 事件的不同任务数 `/ 12` |
| 平均成功耗时 | 成功任务的 `duration_ms` 均值 |
| 平均 Agent 轮数 | 成功任务的 `agent_turns` 均值 |

取消和失败任务留在分母中。`finished` 包括成功、失败和取消。指标只评价当前固定样例与本次配置，不能代表真实仓库或公开基准上的能力。任务提交接口可单独运行评测样例或原有演示样例；当前版本总共只接受这 13 个受控样例，尚未开放任意仓库。

一轮评测可能产生 12 次 DeepSeek 调用，超时重试可能增加调用次数。页面按钮明确标注费用；项目不会自动启动真实评测。

## 展示页面与接口

打开 `http://localhost:8000/` 可提交任务、取消任务、查看进度时间线、代码差异、测试输出，以及评测结果。页面不依赖外部 CDN。接口包括：

- `GET /api/v1/cases`：列出 12 个评测样例。
- `GET /api/v1/tasks`：最近任务列表。
- `GET /api/v1/tasks/{id}/events`：可续读的 SSE 进度流。
- `POST /api/v1/evaluations`：创建评测。
- `GET /api/v1/evaluations`、`GET /api/v1/evaluations/{id}`：评测摘要和逐例结果。

## 已验证与待验证

- 12 个样例逐一验证了修复前测试失败、参考修复后测试通过；AST 守卫拒绝文件和网络访问代码。
- 本地接口检查覆盖评测创建、指标计算、SSE 事件续读与页面返回；项目测试共 13 项通过。
- Alembic 从阶段 3 版本升级到 `0003_progress_evaluations`，旧任务阶段回填已在临时 SQLite 数据库验证。
- 展示页脚本通过 Node 语法检查。
- 2026-11-06 在 Windows Docker Desktop + WSL 2 上完成 PostgreSQL、Redis、API、Worker、Beat 和 Sandbox 的 Compose 联调；成年边界演示任务修复成功，修复后 3 个测试通过。
- 首轮真实 12 项 DeepSeek 评测完成 12/12，成功 11 项，成功率 91.7%。失败项为“剩余库存”：修复前测试按预期失败，Agent 在 6 轮限制内未完成，SDK 返回 `error_max_turns`；旧版本将其记录为通用 `agent_execution_failed`。
- 已明确 Agent 工具的受限语法提示，将轮数上限调至 8，并把轮数耗尽分类为 `agent_max_turns`。仅重测“剩余库存”后，该单项在 4 轮内修复成功，修复后 3 个测试通过。首轮评测记录仍保留原始 91.7%，未把单项复测计入首轮指标。
