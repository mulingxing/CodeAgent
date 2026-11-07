# CodeAgent

一个受控的 AI Coding Agent 后端任务系统。项目以 Claude Agent SDK 接入 DeepSeek，通过 API 提交任务，由 Celery Worker 异步执行，再把状态、代码差异、测试结果和错误写入数据库。

## 已实现

- FastAPI：提交任务、查询任务、健康检查。
- PostgreSQL：保存任务状态和 Agent 产物；Alembic 管理表结构。
- Redis + Celery：把耗时任务移出 API 进程，由独立 Worker 执行。
- Claude Agent SDK + DeepSeek：通过受限的进程内工具修复样例代码。
- 任务幂等领取、Worker 异常后的过期任务回收、执行超时和最多 3 次 Worker 执行尝试。
- 任务取消、Agent 超时后延迟重试，以及数据库 Outbox + Celery Beat 补投。
- 仓库修复可选择 pytest、直接运行 Python 脚本，或仅进行人工验证；运行输出和验证状态会随任务保存。
- pytest：修复前确认所选测试失败，修复后在隔离环境中复测并保存输出。
- 每次测试使用独立 Docker 容器，关闭网络、只读挂载样例并限制 CPU、内存和进程数。
- Docker Compose：编排 API、Worker、Beat、内部 Sandbox 服务、PostgreSQL 和 Redis。
- 任务进度事件与 SSE 续读；12 个受控 Bug 样例的批量评测、指标查询和本地展示页面。
- 本地 Git 仓库任务：提交 Bug 描述和验证方式；Worker 从指定提交提取只读快照，Agent 可修改多个 Python 源文件，保存候选补丁和验证结果。
- 人工审查流程：页面生成独立 Git 分支与工作树，明确点击后快进合并回原分支，并清理审查工作树。

## 当前范围

任务可选择年龄边界演示、12 个受控单函数样例，或 `local_repos` 中的本地 Git 仓库。固定样例仍使用 AST 白名单；仓库任务只开放仓库内已跟踪的 Python 源文件给 Agent，禁止编辑测试，且不开放 Bash。仓库任务支持多文件修改。pytest 模式验证用户所选的 1～8 个测试文件；直接运行模式在隔离环境中执行指定的已跟踪 Python 脚本；人工模式只做 Python 语法检查，由用户审查差异。pytest 和直接运行检查由内部 Sandbox 服务启动独立 Docker 容器；Agent SDK 仍在普通权限的 Worker 进程内运行。

## 阶段 5：本地 Git 仓库

页面以本地仓库修复为主；固定样例与 12 项评测放在折叠的“开发验证”区，供检查执行链路使用。仓库任务可选择：

| 验证方式 | 提交内容 | 结果含义 |
| --- | --- | --- |
| pytest | 1～8 个已跟踪的 `tests/test_*.py` 文件 | 所选测试修复前至少有一项失败、修复后全部通过才算验证通过；Agent 不修改测试文件。 |
| 直接运行 | 一个已跟踪的 Python 脚本、可选参数及预期标准输出片段 | 脚本在隔离容器中运行；有明确预期且满足时才算验证通过。仅正常退出表示运行成功，仍需人工判断逻辑。 |
| 人工验证 | Bug 描述 | Agent 修改后检查 Python 语法，展示差异并标记“待人工验证”。 |

任务详情中的代码差异展示该任务实际修改的源码，并逐行列出原文件与修复后文件的行号；删除行只有原行号，新增行只有修复后行号。验证失败时保留候选差异和错误输出供检查；Agent 最多依据失败输出再修一次。审查分支位于原仓库之外，只有人工点击合并才会修改原分支。如果练习仓库由同一个旧提交复制而来，又运行同一测试，那么两次任务可能产生完全相同的补丁。请用仓库名、任务 ID 和基准提交区分任务记录。

本机已准备 `local_repos/demo_shop`，它是包含两个 Bug 的独立演示仓库。若在新电脑上缺少它，可运行 `.\.venv\Scripts\python.exe scripts\create_demo_repo.py` 创建；脚本发现同名目录已存在时会停止，不会覆盖。自己的项目需要先有 Git 提交，将**整个仓库文件夹（包括 `.git`）**放在 `local_repos` 下的一级子目录，例如 `local_repos/my_project`。目录名只允许英文字母、数字、下划线和连字符。pytest 模式支持已跟踪的 `tests/test_*.py`；直接运行模式选择仓库里已有的 `.py` 脚本。所需依赖应能由容器现有 Python 环境满足；目前不自动安装各仓库的依赖。

1. 在前台运行 `docker compose up --build`，打开 `http://localhost:8000/`，点击“进入工作台”。在“发起修复”中选择仓库与验证方式，描述 Bug 后提交。会调用 DeepSeek API 并产生费用。仓库源码会发送给 DeepSeek；代码检查在无网络、无密钥的隔离容器运行。结束时在该终端按 `Ctrl+C`。
2. 等待任务显示“待生成审查”，检查“代码差异”和验证输出。原仓库文件此时仍是旧版本。人工模式或没有明确预期的直接运行模式会标记为待人工验证。
3. 点击“生成审查分支和工作树”。任务会显示“待人工审核”；详情页可逐个选择改动文件，查看该文件在原仓库和审查工作树中的完整本机路径。原仓库位于 `local_repos/<仓库名>`，修复结果位于 `reviews/<仓库名>-<任务 ID 前八位>`，分支名为 `codeagent/<任务 ID>`。如果项目被移动，可展开“路径显示设置”修改本机项目目录；这个设置只改变页面显示的路径。工作树使用相对路径链接，在 Windows 上用 Git 操作它时需要 Git 2.48 或更新版本。
4. 确认修改后，点击“确认合并到原仓库”。这里的“原仓库”是任务选中的 `local_repos/<仓库名>`；若它是从其他项目复制的试验副本，合并不会回写最初的项目目录。系统要求原分支和审查工作树都未变化，执行快进合并；成功后删除审查工作树和临时分支。数据库中的任务记录、代码差异和测试结果仍保留。若审查工作树有未提交修改，系统会拒绝合并和清理，避免丢失文件。

首次测试建议只用 `demo_shop`。不想合并时，停在第 2 或第 3 步即可；请勿直接删除 `reviews` 目录，否则 Git 会残留工作树记录。

学习系统设计时可按 [项目学习导图](docs/PROJECT_LEARNING_GUIDE.md) 的顺序阅读代码。
两类真实仓库问题的基线、修复与审核范围记录在 [真实仓库试验记录](docs/REAL_REPO_EVALUATION.md)。

## 三种验证方式的演示仓库

需要练习时，在项目根目录运行 `.\.venv\Scripts\python.exe scripts\create_validation_demos.py`。脚本会新建三个独立、已提交的 Git 仓库；任一同名目录已存在时会停止，不会覆盖。页面点击“刷新”即可看到它们。脚本也支持 `--output-dir` 指向其他目录，便于先在临时位置检查内容。

| 仓库 | 验证方式与填写内容 | Bug 描述示例 |
| --- | --- | --- |
| `demo_algorithm` | pytest；`tests/test_search.py` | 二分查找没有检查最后一个候选位置，单元素和最后元素的测试失败。 |
| `demo_numeric` | 直接运行；入口 `src/simulate.py`，参数留空，预期输出 `distance=5.00` | 匀速 2.5、每步 0.2 秒、运行 10 步，应走 5.00；现在累加公式错误。 |
| `demo_plot_svg` | 直接运行；入口 `src/plot_svg.py`，参数留空，预期输出 `points="0,80 40,40 80,0"` | 画出的折线纵坐标方向反了；0、0.5、1.0 应从下到上绘制。 |

画图示例把 SVG 文本打印到运行输出中，可检查折线坐标。当前系统没有文件产物下载或图片预览功能，直接运行模式也不允许向只读测试工作区写文件。以上示例只用 Python 标准库，避免额外依赖影响演示。

## 准备

需要 Docker Desktop（或 Linux Docker Engine）和 DeepSeek API Key。在项目根目录准备 `.env`：

```dotenv
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_MODEL=deepseek-flash[1m]
POSTGRES_DB=codeagent
POSTGRES_USER=codeagent
POSTGRES_PASSWORD=change-this-local-password
```

本机已有 `.env` 时不要用模板覆盖它。密钥不应提交到 Git；`.env` 已忽略。

Windows 本机 Python/SDK 配置和阶段 0 的真实 Agent 验证见 `docs/STAGE0_VALIDATION.md` 与本文件后面的“阶段 0”。Docker Compose 会在 Linux 容器中运行 Celery Worker，Windows 原生 Celery Worker 不在支持范围内。

## 启动服务

```powershell
docker compose up --build
```

API 容器等待 PostgreSQL 和 Redis 健康后，先执行 `alembic upgrade head`，然后启动 FastAPI。Worker、Beat 和内部 Sandbox 服务随后启动。展示页面为 `http://localhost:8000/`，交互式接口文档为 `http://localhost:8000/docs`。

提交任务：

```powershell
$body = @{ repo_id = 'age-boundary-demo'; prompt = 'Fix the age boundary so age 18 is treated as an adult.' } | ConvertTo-Json
$task = Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/api/v1/tasks' -ContentType 'application/json' -Body $body
$task
```

查询任务（将响应中的 ID 替换进 URL）：

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/tasks/$($task.id)"
```

典型状态流转：`queued → running → succeeded`，也可能进入 `failed` 或 `cancelled`。成功时返回 `result_summary`、`diff`、`test_output`、耗时和 Agent 轮数。提交接口返回 HTTP 202；Redis 暂不可用时任务仍保留为 `queued`，Beat 会在 Redis 恢复后补投。

取消任务：

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/tasks/$($task.id)/cancel"
```

排队任务会立即停止投递。运行中的任务立即标记为 `cancelled`，Worker 在下一个检查点停止继续处理，并丢弃迟到的结果；正在进行的模型请求可能仍会继续消耗少量用量。固定样例最长 180 秒，仓库任务最长 300 秒。

查看可用评测样例和实时进度：

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/api/v1/cases'
curl.exe -N 'http://localhost:8000/api/v1/tasks/你的任务ID/events'
```

启动一轮 12 个样例的评测（会调用 DeepSeek API 并产生费用）：

```powershell
$run = Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/api/v1/evaluations'
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/evaluations/$($run.id)"
```

评测指标只描述这 12 个固定样例：任务成功率、Agent 修改率、测试通过率、平均耗时和平均轮数。评测任务走与普通任务相同的 Outbox、Worker 和 Sandbox 流程。完整定义见 [docs/STAGE4_PROGRESS_EVAL.md](docs/STAGE4_PROGRESS_EVAL.md)。

前台启动的服务，在运行 `docker compose up --build` 的终端按 `Ctrl+C` 即可停止。如需移除已停止的 Compose 容器与网络，再运行：

```powershell
docker compose down
```

PostgreSQL 数据保存在 Compose volume 中。清理数据需额外执行 `docker compose down -v`，这会删除该项目数据库和 Redis 持久化数据。

## 阶段 0：本机环境与 Agent 验证

项目内环境脚本安装 uv、Python 3.12.16 和依赖，不修改系统 PATH：

```powershell
.\scripts\setup.ps1
.\scripts\run_stage0.ps1
```

真实 Agent 请求会产生 DeepSeek API 用量。阶段 0 的执行记录见 [docs/STAGE0_VALIDATION.md](docs/STAGE0_VALIDATION.md)。

## 本地开发提示

本地默认数据库是 SQLite，配置可通过环境变量覆盖。完成依赖同步后，可在项目根目录执行 `.\.venv\Scripts\alembic.exe upgrade head` 创建本地表，再运行 `.\.venv\Scripts\uvicorn.exe codeagent.main:app --reload`；提交任务仍需要 Redis 和可运行的 Celery Worker。Celery Worker 建议通过上面的 Compose 启动。本机阶段 0 脚本使用本地固定测试，Compose Worker 使用 Docker 测试容器。

## 已知边界

- 仓库模式当前限于已提交、干净的本地 Python Git 仓库；pytest 模式可选择 1～8 个已跟踪的测试文件。限制快照 8 MB / 500 文件，仅修改已有 Python 源文件，不安装仓库依赖，也不支持远程 GitHub 仓库自动导入。直接运行模式只支持受限的 Python 脚本，不接收任意 Shell 命令；复杂项目可能需要后续适配。
- 没有用户认证；服务仅绑定本机回环地址。SSE 展示数据库保存的阶段事件和状态，不包含模型原始输出或逐 token 日志。
- 内部 Sandbox 服务挂载 Docker socket 并以 root 身份启动，因此该服务拥有很高的宿主机权限。Worker 不持有 Docker socket。测试容器无网络、只读挂载快照、无模型密钥并有限额；此配置适合本人可信代码的本地学习，不作为运行陌生仓库的生产安全边界。
- 已在 Windows Docker Desktop + WSL 2 上完成 Compose 六服务联调和真实 Agent 任务验证。首轮 12 项评测为 11/12；“剩余库存”曾因 Agent 达到轮数上限失败，调整工具提示后单项复测通过。首轮评测记录保留原始结果。

第三阶段设计与验证记录见 [docs/STAGE3_RELIABILITY.md](docs/STAGE3_RELIABILITY.md)。
