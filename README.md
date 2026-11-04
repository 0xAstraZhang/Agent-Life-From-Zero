# Agent Life From Zero

从零学习 Agent 的中文教程与最小 Python 示例。学习路线见 [Agent Learning Hub](docs/Agent-Learning-Hub.md)；实操教程是 [《照着 Claude Code 写一个 Agent》](docs/README.md)：拆解 Claude Code 的架构，分 12 章用 Python 从零写出一个 coding agent（minicode）。

## 运行现有示例

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。在仓库根目录运行：

```bash
uv sync --locked
```

以下是 DeepSeek 的配置示例。请使用自己的 API Key，并按服务商当前文档核对模型和接口地址；实际调用会产生服务商费用。

```bash
export LLM_BASE_URL="https://api.deepseek.com"
export LLM_MODEL="deepseek-flash"
read -rsp 'API Key: ' LLM_API_KEY; echo
export LLM_API_KEY
uv run agent-life-from-zero/chatbot.py
uv run agent-life-from-zero/extract_json.py
```

上述环境变量命令适用于 Bash。脚本读取环境变量，不会自动读取 `.env` 文件。不要将 API Key 写入代码或提交到 Git。连接 OpenAI 时可省略 `LLM_BASE_URL`，但仍需显式设置 `LLM_MODEL` 为账号可用的模型；其他兼容服务的功能和参数支持不一定相同。

- `chatbot.py`：多轮对话，输入 `exit` / `quit` 或结束标准输入退出。
- `extract_json.py`：启用 JSON mode 后，继续验证 `sample`、`measure` 和有限数值 `value`；格式或字段失败时最多重试两次。网络、认证等 API 错误会直接抛给调用方，不是生产级重试实现。
- 教程的代码按章节逐步写进 `agent-life-from-zero/minicode/`，每章都附有用假模型的离线测试；`pytest`、`mcp` 等依赖在用到的章节里安装。环境准备见 [教程目录](docs/README.md#开始之前环境准备)。

官方参考：[OpenAI Python SDK](https://github.com/openai/openai-python)、[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。JSON mode 不等于字段 schema 校验，空白输出和截断输出也需要处理。

## 随附参考资料

`docs/Hello-Agents-V1.0.3-20260717.pdf` 是第三方 Hello-Agents 教材的 2026-07-17 快照（720 页），不是本仓库逐页维护的当前文档。本次教程勘误未对该 PDF 做逐页全面复核，也未修改其内容；请以 [Hello-Agents 上游仓库](https://github.com/datawhalechina/hello-agents) 的当前版本为准。
