# 阶段 3：可靠性与执行环境

## 本阶段实现

| 功能 | 行为 |
| --- | --- |
| 持久化投递 | API 在同一数据库事务写入任务和 Outbox；投递成功后标记已发布 |
| 故障补投 | Celery Beat 每 15 秒触发扫描，Redis 恢复后补投待发布任务；重复消息由 Worker 状态领取条件处理 |
| Agent 超时重试 | 仅 `agent_timeout` 自动重试，延迟随尝试次数递增，总执行次数最多 3 次 |
| 任务取消 | `POST /api/v1/tasks/{id}/cancel`；排队和运行中任务标记 `cancelled`，Worker 不保存迟到结果 |
| 固定测试隔离 | 内部 Sandbox 服务为每次 pytest 启动独立 Docker 容器，挂载只读工作区并禁用网络，限制资源 |

取消是协作式的：API 立即改变任务状态，Worker 在下一次检查点结束处理。正在进行的 SDK 请求无法保证瞬时终止，但有 180 秒整体时限。完成或失败的任务不能再取消，接口返回 HTTP 409；重复取消已取消的任务返回 HTTP 200。

## 测试容器约束

Worker 只把固定工作区名称发送给 Compose 内部的 Sandbox 服务。Sandbox 服务通过 Docker SDK 启动同一个 `codeagent-app:local` 镜像的新容器。测试容器没有 DeepSeek Key，网络模式为 `none`，工作区卷以只读方式挂载，根文件系统只读，使用非 root 用户、丢弃 Linux capabilities，并限制内存 256 MiB、CPU 1 核、进程数 64、执行等待 35 秒。运行结束后强制删除容器。

内部 Sandbox 服务挂载 `/var/run/docker.sock`，并以 root 运行以访问该 socket；Worker 保持非 root，且不持有 Docker socket。Sandbox 服务不公开宿主机端口，也不接收任意命令或镜像名。Docker socket 仍拥有宿主机级别权限，因此本方案适合本地项目演示，不足以安全执行用户提交的任意仓库。Agent SDK 本身仍在 Worker 中运行，当前只允许固定样例的受限工具调用。

## 验证记录

- 项目依赖已重新锁定并同步，包含 Docker SDK。
- 本地 SQLite 测试覆盖 Redis 投递故障后的 Outbox 补投、排队取消、运行中取消、Agent 超时重试、Sandbox 路径拒绝；现有固定样例检查一并通过，共 10 项。
- Alembic 在临时 SQLite 数据库上成功升级至 `0002_create_outbox`，并确认旧版本的排队任务会被回填到 Outbox。
- 当前机器未安装 Docker，因此 PostgreSQL/Redis/Beat/Worker/Sandbox 的 Compose 联调和真实 Docker 测试容器运行仍待在 Docker 环境验证。
