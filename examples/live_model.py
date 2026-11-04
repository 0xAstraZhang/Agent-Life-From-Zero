"""第 03 章起可运行的真实工具往返；只读取一整组环境配置。"""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit

from minicode.agent import Agent
from minicode.providers import ModelConfig, create_model
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec

APIS = ("openai-chat", "openai-responses", "anthropic-messages")


def parser():
    p = argparse.ArgumentParser(description="用真实模型完成 add(2, 3) 工具往返")
    p.add_argument("--api", choices=APIS)
    p.add_argument("--env-prefix", choices=("MINICODE", "LLM"))
    p.add_argument("--provider")
    p.add_argument("--base-url", help="显式覆盖所选配置组的 API 根地址")
    p.add_argument("--max-output-tokens", type=int, default=1024)
    p.add_argument("--chat-token-field", choices=("max_tokens", "max_completion_tokens"))
    p.add_argument("--stream", action="store_true", help="从第 08 章开始使用")
    return p


def config_from_env(args, environ=None):
    env = os.environ if environ is None else environ
    # 任意 MINICODE 凭据字段已设置，就要求该组完整；不能拿另一个来源的 key 补洞。
    prefix = args.env_prefix or (
        "MINICODE" if any(env.get(f"MINICODE_{key}") for key in ("MODEL", "BASE_URL", "API_KEY")) else "LLM")
    missing = [f"{prefix}_{key}" for key in ("MODEL", "BASE_URL", "API_KEY")
               if not env.get(f"{prefix}_{key}")]
    if missing:
        raise ValueError("请完整设置同一组环境变量，缺少：" + ", ".join(missing))
    api = args.api or env.get(f"{prefix}_API", "openai-chat")
    base_url = args.base_url or env[f"{prefix}_BASE_URL"]
    url = urlsplit(base_url)
    known_deepseek = url.hostname == "api.deepseek.com"
    provider = args.provider or env.get(f"{prefix}_PROVIDER") or ("deepseek" if known_deepseek else "custom")
    # 这里只处理已知服务的已核实路由；其他服务地址由用户明确提供。
    if known_deepseek and url.path.rstrip("/") in {"", "/v1", "/anthropic", "/anthropic/v1"}:
        path = "/anthropic/v1" if api == "anthropic-messages" else ""
        base_url = urlunsplit((url.scheme, url.netloc, path, url.query, url.fragment))
    return ModelConfig(api=api, model=env[f"{prefix}_MODEL"], base_url=base_url,
                       api_key=env[f"{prefix}_API_KEY"], provider=provider,
                       max_tokens=args.max_output_tokens,
                       chat_token_field=args.chat_token_field or env.get(f"{prefix}_CHAT_TOKEN_FIELD"))


def run_live(config, *, stream=False, client=None):
    executed = []

    def add(args, context):
        result = args["a"] + args["b"]
        executed.append(result)
        return str(result)

    spec = ToolSpec("add", "把两个整数相加", {
        "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False})
    runner = ToolRunner([Tool(spec, add, read_only=True)], ToolContext(Path.cwd()))
    model = create_model(config, client)
    try:
        # 第 03–05 章 Agent 没有 stream 参数；默认路径不能提前要求它。
        agent_options = {"stream": True} if stream else {}
        agent = Agent(model, runner, max_turns=3, **agent_options)
        final = None
        for event in agent.run("必须调用 add 工具计算 2 加 3，再只回答数字结果。"):
            if event["type"] == "result":
                final = event
        if final is None:
            raise RuntimeError("没有收到任务终止事件")
        passed = final["status"] == "success" and executed == [5] and final["text"].strip() == "5"
        # 不输出 assistant 的原始 metadata、密钥或推理内容。
        return {"api": config.api, "model": config.model, "stream": stream,
                "status": final["status"], "tool_calls": len(executed),
                "answer": final["text"], "usage": final.get("usage", {}), "passed": passed}
    finally:
        model.close()


def main(argv=None):
    config = None
    try:
        args = parser().parse_args(argv)
        config = config_from_env(args)
        result = run_live(config, stream=args.stream)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["passed"] else 1
    except Exception as error:
        message = str(error)
        if config is not None:
            message = message.replace(config.api_key, "<redacted>")
        print(f"live_model: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
