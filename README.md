## Agent Life From Zero

从零学习 Agent 的中文教程与最小 Python 示例。拆解 Claude Code 的架构，分 12 章用 Python 从零写出一个 Coding Agent。

### 运行现有示例

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

脚本读取环境变量，不会自动读取 `.env` 文件。  
不要将 API Key 写入代码或提交到 Git。  
连接 OpenAI 时可省略 `LLM_BASE_URL`，但仍需显式设置 `LLM_MODEL` 为账号可用的模型；其他兼容服务的功能和参数支持不一定相同。

- `chatbot.py`：多轮对话，输入 `exit` / `quit` 或结束标准输入退出。
- `extract_json.py`：启用 JSON mode 后，继续验证 `sample`、`measure` 和有限数值 `value`；格式或字段失败时最多重试两次。网络、认证等 API 错误会直接抛给调用方，不是生产级重试实现。
- 教程的代码按章节逐步写进 `agent-life-from-zero/minicode/`，每章都附有用假模型的离线测试；`pytest`、`mcp` 等依赖在用到的章节里安装。环境准备见 [教程目录](docs/README.md#开始之前环境准备)。
