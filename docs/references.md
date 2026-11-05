# 参考实现：读源码时看哪一层

[返回目录](README.md)

本教程参考三个开源项目的分层方法，Python 实现按教学需要重新组织。下面固定的是本次改写核对过的提交，避免仓库目录调整后教程失去依据。核对日期：2026-11-04。

## pi：统一消息和模型接口

旧仓库 `badlogic/pi-mono` 已重定向到 `earendil-works/pi`。本教程参考提交 `676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d`。

- [统一消息、工具、上下文与事件](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/types.ts)：对应第 01、02 章的内部类型。
- [Agent 类型与转换边界](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/agent/src/types.ts)：观察模型可见工具与可执行工具怎样关联。
- [Agent Loop](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/agent/src/agent-loop.ts)：观察模型输出、执行和结果回传的先后关系。
- [Chat Completions](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/api/openai-completions.ts)、[Responses 转换](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/api/openai-responses-shared.ts)、[Anthropic Messages](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/api/anthropic-messages.ts)：对应第 03–05、08 章。

读的时候只追踪一次完整往返：内部工具定义如何编码、调用如何归一、结果如何回到下一次请求。minicode 先支持文本和本地函数工具，后续能力需要继续扩展内部内容块。

## OpenCode：协议、来源、传输分开

参考提交 `59e95cc5394b7714451608099ef4db7bffc904b6`。这个版本的相关实现位于 `packages/ai`，不使用旧版 `packages/opencode/src/session` 路径。

- [Message 与内容块](https://github.com/anomalyco/opencode/blob/59e95cc5394b7714451608099ef4db7bffc904b6/packages/ai/src/schema/messages.ts)：消息内容可以包含文本、调用、结果和服务商元数据。
- [工具声明与执行](https://github.com/anomalyco/opencode/blob/59e95cc5394b7714451608099ef4db7bffc904b6/packages/ai/src/tool.ts)：用于比较 `ToolSpec` 和 `Tool` 的边界。
- [Chat 协议](https://github.com/anomalyco/opencode/blob/59e95cc5394b7714451608099ef4db7bffc904b6/packages/ai/src/protocols/openai-chat.ts)、[Responses 协议](https://github.com/anomalyco/opencode/blob/59e95cc5394b7714451608099ef4db7bffc904b6/packages/ai/src/protocols/openai-responses.ts)、[Anthropic 协议](https://github.com/anomalyco/opencode/blob/59e95cc5394b7714451608099ef4db7bffc904b6/packages/ai/src/protocols/anthropic-messages.ts)：协议转换与 endpoint/auth/transport 的组合分开。
- [循环辅助](https://github.com/anomalyco/opencode/blob/59e95cc5394b7714451608099ef4db7bffc904b6/packages/ai/src/loop.ts)：观察未完成输出如何影响工具执行，以及历史如何保持调用与结果的关系。

minicode 从这里借鉴的是边界：配置 `api` 和服务商来源各自独立；保留专有续轮数据，但不让 Agent 核心解释这些数据。

## Codex：运行时与工具路由

参考提交 `93b4e5b4c6474cdbf12614c4ac3acbd006fbee89`。

- [WireApi](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/model-provider-info/src/lib.rs)：这个版本只支持 Responses，不能作为三协议兼容已经完成的证据。
- [Prompt 与 ResponseStream](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/core/src/client_common.rs)：观察客户端边界与流事件。
- [ResponseItem](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/protocol/src/models.rs)：消息、reasoning、函数调用与结果分别表达；item ID 和 call ID 不混用。
- [ToolRouter](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/core/src/tools/router.rs)：模型可见声明与执行注册表分离。
- [Linux 沙箱的 bwrap 参数](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/linux-sandbox/src/bwrap.rs)、[受保护的工作区元数据路径](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/protocol/src/permissions.rs)、[沙箱拦截的推测](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/sandboxing/src/denial.rs)：对应第 17 章。

minicode 保留了较小的串行执行器、显式审批和只读委派。第 17 章只实现了 Linux 上基于 bubblewrap 的基础沙箱；跨平台沙箱、按域名放行网络、复杂调度和工作区隔离需要单独实现，不能因为参考了 Codex 就声称具备这些能力。

## 协议的一手资料

- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI Responses 迁移说明](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [OpenAI Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Anthropic 工具调用往返](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)
- [Anthropic Streaming Messages](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [MCP 2025-11-25 生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP 2025-11-25 工具](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

API 文档用于确定字段和行为；开源源码用于理解怎么组织实现。遇到兼容服务时，以该服务实际支持的协议能力为准，再增加对应 fixture 和在线检查。
