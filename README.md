# Agent Life From Zero

从零学习 Agent 的中文教程与最小 Python 示例。学习路线见 [Agent Learning Hub](docs/Agent-Learning-Hub.md)，分阶段教程位于 `docs/Stage 1` 至 `docs/Stage 8` 对应的 Markdown 文件中。

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
- 教程中的代码是分步骤教学片段，不等于已经整合好的完整应用。浏览器、检索和其他进阶章节依赖需按对应章节安装。

官方参考：[OpenAI Python SDK](https://github.com/openai/openai-python)、[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。JSON mode 不等于字段 schema 校验，空白输出和截断输出也需要处理。

## 离线测试

无需 API Key，不发出模型请求：

```bash
uv run python -m unittest discover -s tests -p 'test_examples.py' -v
```

### 教程完整代码的回归测试

下列命令在独立虚拟环境中安装测试依赖，提取 Markdown 的完整程序到临时目录，再运行章节中的离线测试。无需配置模型 Key；不会调用付费模型。浏览器测试使用本地模拟页面，但首次安装依赖和 Chromium 需要联网。

```bash
uv venv .venv-tutorial-tests
uv pip install --python .venv-tutorial-tests/bin/python pytest openai httpx pydantic langgraph pyyaml 'mcp[cli]>=2,<3' playwright
.venv-tutorial-tests/bin/python -m playwright install chromium
.venv-tutorial-tests/bin/python -m pytest tests/test_tutorial_early.py tests/test_stage6_logic.py -q
.venv-tutorial-tests/bin/python scripts/check_tutorials_456.py
.venv-tutorial-tests/bin/python scripts/check_tutorials_78.py
```

以上路径适用于 macOS/Linux；Windows 将 `.venv-tutorial-tests/bin/python` 换成 `.venv-tutorial-tests/Scripts/python.exe`。这组依赖独立于示例脚本的 `uv.lock`，并非线上 API、联网检索或所有服务商兼容性验证。若环境不允许启动 Chromium，Stage 6 的完整浏览器测试不能视为通过；独立逻辑回归也不能替代它。已安装系统 Chromium 时，可通过 `TUTORIAL_CHROMIUM_EXECUTABLE` 指定其可执行路径。

## 随附参考资料

`docs/Hello-Agents-V1.0.3-20260717.pdf` 是第三方 Hello-Agents 教材的 2026-07-17 快照（720 页），不是本仓库逐页维护的当前文档。本次教程勘误未对该 PDF 做逐页全面复核，也未修改其内容；请以 [Hello-Agents 上游仓库](https://github.com/datawhalechina/hello-agents) 的当前版本为准。
