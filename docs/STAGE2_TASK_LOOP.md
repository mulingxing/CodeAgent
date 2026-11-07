# 阶段 2：后端任务闭环

## 请求流

1. `POST /api/v1/tasks` 校验固定仓库 ID 和任务描述，在数据库创建 `queued` 记录。
2. API 将数据库任务 ID 投递到 Redis/Celery 队列，返回 HTTP 202 和任务查询地址。
3. Worker 用条件更新把任务从 `queued` 改为 `running` 并增加尝试次数；重复投递不会并行重复执行。
4. Worker 调用 Claude Agent SDK + DeepSeek 执行受控样例，校验修改后的 Python AST，运行 pytest。
5. Worker 保存 `succeeded` 或 `failed`、差异、测试输出、耗时、轮数及可公开错误信息。
6. `GET /api/v1/tasks/{id}` 从 PostgreSQL 读取任务进度与结果。

## 端点

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 检查 API 与数据库连接 |
| `POST` | `/api/v1/tasks` | 校验并提交异步任务，返回 202 |
| `GET` | `/api/v1/tasks/{task_id}` | 查询状态或完整执行结果 |

任务状态为 `queued`、`running`、`succeeded`、`failed`。数据库是任务状态的事实来源；Celery 只传任务 ID，Celery result backend 不承担业务结果存储。

## 可靠性处理

- Worker 领取时执行带状态条件的 UPDATE，避免重复消息同时执行。
- Worker 启用 late ack 和 worker lost requeue。Redis visibility timeout 是 600 秒；超过 360 秒仍处于 `running` 的记录可被重新领取，最多允许 3 次 Worker 崩溃执行。
- SDK 单次调用限制为 180 秒，Celery 软/硬时限为 270/300 秒。模型拒绝、API 错误、测试失败等业务失败直接保存失败状态，不将任意错误细节返回给用户。
- 阶段 2 的 API 先提交数据库再投递 Redis，进程在这两个动作之间崩溃可能留下未投递任务。阶段 3 已加入事务 Outbox 和 Beat 补投；当前行为见 `STAGE3_RELIABILITY.md`。

## 文件位置

- `src/codeagent/api.py`：FastAPI 路由与依赖注入。
- `src/codeagent/models.py`、`schemas.py`：持久化模型与接口结构。
- `src/codeagent/tasks.py`、`celery_app.py`：Worker 任务和 Celery 配置。
- `src/codeagent/runner.py`：受控 Agent 执行、AST 校验、pytest 与 diff。
- `migrations/versions/0001_create_tasks.py`：初始数据库迁移。
- `compose.yaml`、`Dockerfile`：本地服务编排。
