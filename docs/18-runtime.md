# 第 18 章：运行控制与可观测性

上一章：[沙箱](17-sandbox.md) · [目录](README.md)

## 本章的完整累积目录

```bash
uv run python scripts/check_tutorial.py --chapter 18 --export /tmp/minicode-ch18
cd /tmp/minicode-ch18
uv sync
uv run python examples/18_runtime.py
uv run python -m pytest tests -q
```

第 00–17 章继续使用对应阶段的快照。本章替换为最终实现，新增参数和事件不会提前出现在旧章节里。

## 从“能运行”到“能解释一次运行”

此前的 Agent 已经能调用工具、保存会话、限制累计 token 和运行 Linux 沙箱。要比较不同 Agent 的任务成功率，还需要知道：模型实际收到哪些控制参数，失败前是否重试，任务是否超时，长历史如何缩短，费用是怎样计算的。

本章把这些控制分开：

| 边界 | 负责什么 |
| --- | --- |
| 模型配置和协议适配器 | 参数验证、协议字段、HTTP 请求、重试、原始 usage |
| Agent 和压缩包装器 | 整任务期限、工具调度、历史压缩、完整工具往返 |
| 文件和进程工具 | 分段读取、日志续读、后台进程与清理 |
| CLI 和运行记录 | 明确配置来源、收集事件、按指定价格换算费用 |

## 显式控制模型

`thinking` 表示是否开启思考，`reasoning_effort` 表示服务商支持的推理强度，`max_tokens` 表示单次输出上限。三个参数分别配置。保留上轮推理块是协议续轮所需的数据处理，也独立于思考开关。

```bash
uv run minicode --api anthropic-messages --provider deepseek \
  --model deepseek-flash --base-url https://api.deepseek.com/anthropic/v1 \
  --thinking enabled --reasoning-effort high \
  --max-output-tokens 8192 --read-only \
  -p "阅读 README，说明项目结构"
```

最终 URL 是 `/anthropic/v1/messages`，内部使用的 base URL 包含 `/v1`。CLI 会把已知 DeepSeek 官方地址规范化到所选协议的路径；上例显式写出完整地址，其他自定义服务仍需按其文档提供地址。密钥单独放在 `MINICODE_API_KEY` 中。

后续 CLI 示例复用同一服务配置，可以先设置这些非秘密环境变量：

```bash
export MINICODE_API=anthropic-messages
export MINICODE_PROVIDER=deepseek
export MINICODE_MODEL=deepseek-flash
export MINICODE_BASE_URL=https://api.deepseek.com/anthropic/v1
export MINICODE_THINKING=enabled
export MINICODE_REASONING_EFFORT=high
```

可选控制项包括采样参数 `--temperature`、`--top-p`，工具策略 `--tool-choice`、`--strict-tools`，以及最终回复的 `--response-format`、`--response-schema`。这些参数经过服务商和协议兼容检查；支持范围以代码中的校验和所选模型为准。工具执行许可仍由 `--allow-tool` 控制，改变模型的工具选择策略不会自动授予权限。

| 参数 | 本章使用方式 |
| --- | --- |
| `--thinking` | `enabled` 或 `disabled`；也可通过 `MINICODE_THINKING` 设置 |
| `--reasoning-effort` | DeepSeek 显式使用 `low/high/max`；环境变量是 `MINICODE_REASONING_EFFORT` |
| `--thinking-budget-tokens` | 仅用于原生 Anthropic 显式思考预算，不能当作 DeepSeek 的 effort |
| `--tool-choice` | `auto`、`none`、`required` 或 `tool:工具名`，具体支持范围按服务商检查；强制选择只作用到本次用户输入后的第一次工具调用 |
| `--response-format json_object` | 请求 JSON 对象；DeepSeek 当前只开放已确认的 Chat 协议组合 |
| `--response-format json_schema --response-schema FILE` | 加载 JSON Schema，只有支持该格式的服务商和协议才接受 |

最终结构化回复还会在本地解析或验证 schema，格式错误不会被当作成功。工具调用中间轮次仍按工具协议处理。

`required` 和 `tool:工具名` 若在每一轮都生效，模型永远无法给出最终回答，任务只能耗尽轮数。因此强制选择只用于本次用户输入后、第一次工具调用之前的请求；工具结果返回后不再发送 `tool_choice`，交回服务商默认。`auto` 和 `none` 每轮都发送。子 Agent 沿用路由、思考和采样设置，但不继承父任务的工具选择和最终输出格式：它的工具集合不同，返回的是散文摘要。

DeepSeek 的思考模式默认值、effort 档位和采样参数效果由服务端决定。做评测时显式填写需要固定的参数，保存本次有效配置；不要把服务端忽略的参数当作已经控制的变量。参考 [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 和 [Anthropic API 兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)。

## 请求重试与任务期限

`--request-timeout` 控制 HTTP 超时，`--max-retries` 控制可恢复请求错误的重试次数，`--retry-base-delay` 和 `--retry-max-delay` 控制退避等待。认证、参数和协议错误需要修正原因；已向上层输出的流也不能直接从头重放。

`--task-timeout` 限制整个用户任务的时间，覆盖请求、退避和工具执行。它与 `--max-turns`、`--token-budget` 共同限制任务：

```bash
uv run minicode --request-timeout 120 --max-retries 2 \
  --task-timeout 900 --max-turns 40 --token-budget 200000 \
  --allow-tool Read --allow-tool Edit --allow-tool Bash \
  -p "修复失败的测试，并重新运行相关测试"
```

token 预算仍是在下一次请求之前检查的软上限，当前请求可能超额；服务端缺少 usage 时，无法凭本地数据恢复完整账单。任务超时和取消后应查看终止记录与文件状态，再决定如何继续。

在支持 `setitimer` 的 POSIX 主线程中，任务期限能中断阻塞调用。其他平台或工作线程依赖 HTTP、进程工具的下层超时和执行边界检查；自定义工具也必须配合 deadline，不能据此宣称任意第三方工具都能被立即中断。

## 单个长任务的上下文

长任务中只有一次用户输入，也可能产生许多轮模型和工具消息。本章按完整工具往返寻找可压缩边界，保留用户目标、system 消息和最近的上下文，不拆开工具调用与对应结果。

完整历史一旦超过阈值就会一直超过，所以不能每轮都按完整历史重新摘要。压缩后的请求视图仍低于阈值时，沿用上次的切分点和摘要；视图再次超限才移动切分点，并且只把新移出视图的记录与旧摘要一起交给模型续写。历史被修改、前缀对不上时才从头摘要。

`--compact-chars` 保留旧的字符阈值入口；`--context-window` 指定模型上下文窗口，压缩时给 `--max-output-tokens` 预留空间。本地 token 数是估算值，用于提前收缩请求，不能替代服务商返回的实际 usage。

`--parallel-tools N` 只并行执行声明为只读且 `parallel_safe` 的工具，写操作保持串行。所有调用继续经过同一权限和执行包装器，返回历史保持稳定顺序。

## 分段读取和进程会话

Read 可以只读取需要的行范围，避免长文件中间内容被截断后无法继续定位。`offset` 从第 1 行开始，`limit` 默认 200 行；超长单行可按返回提示设置从 0 开始的 `column`。例如模型可以调用：

```json
{"path": "src/server.py", "offset": 120, "limit": 80}
```

命令的大输出保存在进程管理器的私有临时日志中，以日志 ID 引用；日志工具可以分段读取完整内容。日志分页的 `offset` 和 `limit` 单位是字节，返回的 `next_offset` 用于下一次读取。

后台命令通过 BashStart 启动，用 BashPoll 检查状态，BashSend 发送标准输入，BashStop 停止，BashLog 读取日志。它们与同步 Bash 一样需要明确授权。例如：

```bash
uv run minicode --allow-tool Bash --allow-tool BashStart \
  --allow-tool BashPoll --allow-tool BashSend --allow-tool BashStop \
  --allow-tool BashLog --task-timeout 600 \
  -p "启动本地开发服务，检查健康接口，然后停止服务"
```

这些会话使用管道而非 PTY。BashSend 的 `text` 不自动追加换行，`eof=true` 关闭输入；非阻塞 stdin 写入目前只支持 POSIX。CLI 退出时清理所管理的进程；在 Linux 沙箱模式下，新启动的命令仍通过同一 bubblewrap 配置执行。

进程日志只属于当前运行，不是会话恢复文件。恢复对话不会重新连接已经退出的进程。

## 记录用量和费用

`--event-log PATH` 将运行记录写成 JSONL，与 `--session` 保存的对话历史分开。请求记录保留原始 usage 和归一化总数，包含重试、耗时、请求 ID 以及非秘密配置；工具、压缩和任务终止事件也写入同一条运行轨迹。

`--prices PATH` 显式加载价格文件，按输入、输出和缓存分项计算费用。项目不内置默认价格。价表 JSON 必须有以下字段：

| 字段 | 内容 |
| --- | --- |
| `model` | 与本次配置完全一致的模型 ID |
| `currency` | 费用币种 |
| `as_of` | 核对价格的日期，格式为 `YYYY-MM-DD` |
| `input_per_million` | 普通输入每百万 token 的价格 |
| `output_per_million` | 输出每百万 token 的价格 |
| `cache_read_per_million` | 缓存命中每百万 token 的价格 |
| `cache_write_per_million` | 创建缓存每百万 token 的价格 |

缓存分项与普通输入互斥，推理 token 已包含在输出中时不会再次收费。服务商缺少用量时，`estimated_cost` 为 `null`，另保留已知费用小计和未知用量的请求数。价格有时段差异时应按对应时段分别运行或复算。

可以确定没有生成的失败请求按零用量计入，并在请求记录中标记 `unbilled: true`：连接未建立，或服务商以 4xx（包括 429）拒绝。读超时、5xx 和中途断开的流可能已经计费，仍记为未知。

事件日志必须使用新文件路径，避免覆盖上一次结果。它记录控制和计量元数据，不包含提示词、工具参数和工具输出；`--session` 和 `--json` 中的对话内容另按实际需求保存。

线上评测需要保存模型和 Agent 版本、任务版本、配置、日志、最终补丁和独立验收结果。本章新增的离线实验只验证控制和计量链路，任务成功率仍须用真实任务与外部验收获得。

## 验收

```bash
uv run python examples/18_runtime.py
uv run python -m pytest tests -q
uv run python scripts/check_tutorial.py
uv run python -m minicode.evals
uv build
```

真实 DeepSeek 请求需要密钥并产生服务商费用。默认章节实验和自动测试使用假模型、假 HTTP 响应或本地子进程，不调用收费模型。
