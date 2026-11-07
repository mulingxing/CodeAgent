# 阶段 0 真实 Agent 验证

## 结果

在 Windows x64 项目环境中，DeepSeek API 通过 Claude Agent SDK 成功完成一次受控代码修复。

- Python：3.12.16（项目本地）
- uv：0.13.7（项目本地）
- Claude Agent SDK：0.2.178
- SDK 附带 Claude Code CLI：2.1.318
- 模型别名：`deepseek-flash[1m]`
- Agent 轮数上限：6

## 验证过程

1. 原始 `is_adult` 样例对年龄 18 返回错误结果，固定测试按预期失败。
2. Agent 通过 SDK 注册的 `read_code` 工具读取样例，并通过 `write_code` 工具修改文件。
3. 本地 AST 检查只接受预期的单函数实现；通过后由本地 pytest 运行测试。
4. 修复后所有样例测试通过。

```diff
def is_adult(age: int) -> bool:
-    return age > 18
+    return age >= 18
```

## 范围

这次结果证明当前环境、SDK 子进程、DeepSeek Anthropic 兼容端点和受控工具调用可以协同工作。它只验证了一个固定文件和一个任务。之后的阶段 2～4 已补上后端任务系统、取消、故障补投、固定测试的独立 Docker 容器、SSE 进度和受控样例评测；通用仓库修复和 Agent 进程隔离仍未实现。

真实 API 调用按 DeepSeek 实际用量计费。本脚本没有保存本次 token 用量或费用；请在 DeepSeek 平台查看账单。API Key 保存在本机 `.env`，已被 `.gitignore` 忽略，验证输出不包含密钥。
