"""Explicit, provider-aware generation controls; never silently drop a requested option."""
import copy
import math

from jsonschema import Draft202012Validator


CONTROL_FIELDS = ("thinking", "reasoning_effort", "temperature", "top_p", "tool_choice",
                  "strict_tools", "response_format", "response_schema", "thinking_budget_tokens")


def validate_controls(c):
    if c.thinking not in (None, "enabled", "disabled"):
        raise ValueError("thinking 只能是 enabled 或 disabled")
    if c.reasoning_effort not in (None, "none", "minimal", "low", "medium", "high", "xhigh", "max"):
        raise ValueError("未知 reasoning_effort")
    for name, low, high in (("temperature", 0, 2), ("top_p", 0, 1)):
        value = getattr(c, name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or not low <= value <= high):
            raise ValueError(f"{name} 必须在 {low}–{high} 之间")
    if c.tool_choice is not None and (not isinstance(c.tool_choice, str) or
            (c.tool_choice not in ("auto", "none", "required") and
             not (c.tool_choice.startswith("tool:") and len(c.tool_choice) > 5))):
        raise ValueError("tool_choice 应为 auto、none、required 或 tool:工具名")
    if not isinstance(c.strict_tools, bool):
        raise ValueError("strict_tools 必须为布尔值")
    if c.response_format not in (None, "json_object", "json_schema"):
        raise ValueError("response_format 应为 json_object 或 json_schema")
    if c.response_format == "json_schema":
        if not isinstance(c.response_schema, dict):
            raise ValueError("json_schema 需要 response_schema 对象")
        Draft202012Validator.check_schema(c.response_schema)
    elif c.response_schema is not None:
        raise ValueError("response_schema 仅用于 json_schema")
    if c.thinking_budget_tokens is not None:
        if (isinstance(c.thinking_budget_tokens, bool) or not isinstance(c.thinking_budget_tokens, int)
                or c.thinking_budget_tokens < 1024 or c.thinking_budget_tokens >= c.max_tokens):
            raise ValueError("thinking_budget_tokens 必须 >= 1024 且小于 max_tokens")
        if c.provider.lower() != "anthropic" or c.thinking != "enabled":
            raise ValueError("thinking_budget_tokens 仅用于 Anthropic 显式 enabled 思考")
    if c.thinking == "disabled" and c.reasoning_effort not in (None, "none"):
        raise ValueError("关闭 thinking 时不能设置推理强度")
    if c.thinking == "enabled" and c.reasoning_effort == "none":
        raise ValueError("thinking=enabled 与 effort=none 冲突")
    requested = c.strict_tools or any(getattr(c, name) is not None for name in CONTROL_FIELDS if name != "strict_tools")
    provider = c.provider.lower()
    if requested and provider not in ("deepseek", "openai", "anthropic"):
        raise ValueError("自定义 provider 尚无参数能力声明；请显式选择 deepseek/openai/anthropic")
    if provider == "anthropic" and c.api != "anthropic-messages" and requested:
        raise ValueError("Anthropic 控制参数需要 anthropic-messages 协议")
    if provider == "openai" and c.api == "anthropic-messages" and requested:
        raise ValueError("OpenAI 控制参数需要 openai-chat 或 openai-responses")
    if provider == "deepseek":
        if requested and c.model not in ("deepseek-flash", "deepseek-v4-pro"):
            raise ValueError("DeepSeek 参数能力仅确认 deepseek-flash、deepseek-v4-pro；请使用明确模型 ID")
        if c.reasoning_effort not in (None, "low", "high", "max"):
            raise ValueError("DeepSeek effort 使用实际档位 low/high/max，避免隐式映射")
        thinking = c.thinking != "disabled"  # DeepSeek's documented default is enabled.
        if thinking and c.temperature is not None:
            raise ValueError("DeepSeek 思考模式忽略 temperature；请关闭 thinking 或移除该参数")
        if c.top_p is not None:
            if c.model == "deepseek-flash" and (not thinking or c.top_p < .95):
                raise ValueError("DeepSeek Flash top_p 仅在思考模式生效，范围 0.95–1")
            if c.model == "deepseek-v4-pro" and thinking:
                raise ValueError("DeepSeek Pro 思考模式忽略 top_p")
        if c.tool_choice == "required" or (thinking and (c.tool_choice or "").startswith("tool:")):
            raise ValueError("DeepSeek 此模式不保证强制工具调用；使用 auto/none")
        if c.strict_tools:
            raise ValueError("DeepSeek strict 工具需要独立 beta 契约；当前稳定接口不支持")
        if c.response_format and (c.api != "openai-chat" or c.response_format != "json_object"):
            raise ValueError("DeepSeek 当前仅启用已验证的 Chat json_object 格式")
    # Deliberately finite capability profiles; unknown models still work without controls.
    # Sources checked 2026-11-05: OpenAI latest-model?model=gpt-5.4 (parameter compatibility),
    # Claude extended-thinking (4.7+ reject manual enabled) and structured-outputs.
    if provider == "anthropic":
        if requested and c.model not in ("claude-sonnet-4-6", "claude-opus-4-6"):
            raise ValueError("Anthropic 新控制参数目前仅确认 claude-sonnet-4-6 / claude-opus-4-6")
        if c.temperature is not None and c.temperature > 1:
            raise ValueError("Anthropic temperature 范围是 0–1")
        if c.response_format == "json_object":
            raise ValueError("Anthropic 使用 json_schema，不能请求无 schema 的 json_object")
        if c.thinking == "enabled" and c.max_tokens <= (c.thinking_budget_tokens or 1024):
            raise ValueError("Anthropic max_tokens 必须大于思考预算")
        if c.thinking == "enabled" and (c.temperature is not None or c.top_p is not None):
            raise ValueError("显式 Anthropic thinking 不与采样参数组合")
        if c.thinking == "enabled" and c.tool_choice not in (None, "auto", "none"):
            raise ValueError("Anthropic thinking 不支持强制工具选择")
        if c.reasoning_effort not in (None, "low", "medium", "high", "max"):
            raise ValueError("Anthropic effort 使用 low/medium/high/max")
    if provider == "openai":
        efforts = {"gpt-5.4": {"none", "low", "medium", "high", "xhigh"},
                   "gpt-5.2": {"none", "low", "medium", "high", "xhigh"},
                   "o3": {"low", "medium", "high"}, "gpt-4.1": set()}
        if requested and c.model not in efforts:
            raise ValueError("此 OpenAI 模型尚无控制参数能力配置；已确认 gpt-5.4/gpt-5.2/o3/gpt-4.1")
        effort = c.reasoning_effort
        if c.thinking is not None:
            effort = "none" if c.thinking == "disabled" else effort or "medium"
        if effort is not None and effort not in efforts.get(c.model, set()):
            raise ValueError(f"{c.model} 不支持 effort={effort}")
        if c.temperature is not None or c.top_p is not None:
            if c.model == "o3" or (c.model in ("gpt-5.4", "gpt-5.2") and effort not in (None, "none")):
                raise ValueError("此推理模式不支持 temperature/top_p")
        if c.response_format == "json_schema":
            # 请求按 strict=true 发送；在配置阶段拒绝，而不是每次请求时才失败。
            strict_schema(c.response_schema)


_CONFIGURED = object()


def request_controls(c, tools, *, tool_choice=_CONFIGURED):
    """tool_choice 可由适配器按本轮状态覆盖；None 表示本轮不发送。"""
    result = {}
    api, provider = c.api, c.provider.lower()
    for name in ("temperature", "top_p"):
        if getattr(c, name) is not None:
            result[name] = getattr(c, name)
    effort = c.reasoning_effort
    if provider == "openai" and c.thinking is not None:
        effort = "none" if c.thinking == "disabled" else effort or "medium"
    if api == "openai-responses":
        if provider == "deepseek" and c.thinking is not None:
            effort = "none" if c.thinking == "disabled" else effort or "high"
        if effort is not None:
            result["reasoning"] = {"effort": effort}
    else:
        if c.thinking is not None:
            if provider in ("deepseek", "anthropic"):
                result["thinking"] = {"type": c.thinking}
                if provider == "anthropic" and c.thinking == "enabled":
                    result["thinking"]["budget_tokens"] = c.thinking_budget_tokens or 1024
        if effort is not None:
            if api == "anthropic-messages":
                result["output_config"] = {"effort": effort}
            else:
                result["reasoning_effort"] = effort
    choice = c.tool_choice if tool_choice is _CONFIGURED else tool_choice
    if choice is not None:
        if choice not in ("none", "auto") and not tools:
            raise ValueError("强制工具选择需要提供工具")
        name = choice[5:] if choice.startswith("tool:") else None
        if name and name not in {t.name for t in tools}:
            raise ValueError(f"指定工具未提供：{name}")
        if api == "anthropic-messages":
            result["tool_choice"] = ({"type": "tool", "name": name} if name else
                                      {"type": "any" if choice == "required" else choice})
        elif name:
            result["tool_choice"] = ({"type": "function", "function": {"name": name}}
                                     if api == "openai-chat" else {"type": "function", "name": name})
        else:
            result["tool_choice"] = choice
    if c.response_format:
        format_ = {"type": c.response_format}
        if c.response_format == "json_schema":
            schema = copy.deepcopy(c.response_schema)
            if api == "openai-chat":
                format_["json_schema"] = {"name": "result", "strict": True, "schema": schema}
            else:
                format_["schema"] = schema
                if api == "openai-responses":
                    format_.update(name="result", strict=True)
        if api == "openai-chat":
            result["response_format"] = format_
        elif api == "openai-responses":
            result["text"] = {"format": format_}
        else:
            result.setdefault("output_config", {})["format"] = format_
    return result


def strict_schema(schema):
    """Reject schemas the strict contract cannot enforce, without rewriting optional fields."""
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False or set(schema.get("required", [])) != set(schema.get("properties", {})):
            raise ValueError("strict schema 的对象需要 additionalProperties=false 且所有属性列入 required")
    for value in schema.values():
        if isinstance(value, dict):
            strict_schema(value)
        elif isinstance(value, list):
            for item in value:
                strict_schema(item)


def strict_tool_schema(schema, root=None):
    """Represent optional arguments as nullable required fields on the wire only."""
    # null 是否原本合法要按原始根 schema 判断，属性里的 $ref 才能解析到 $defs。
    root = schema if root is None else root
    validator = Draft202012Validator(root if isinstance(root, dict) else {})
    schema = copy.deepcopy(schema)
    if not isinstance(schema, dict):
        return schema
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            schema[key] = [strict_tool_schema(item, root) for item in schema[key]]
    if isinstance(schema.get("items"), dict):
        schema["items"] = strict_tool_schema(schema["items"], root)
    for key in ("$defs", "definitions"):
        if key in schema:
            schema[key] = {name: strict_tool_schema(item, root) for name, item in schema[key].items()}
    if schema.get("type") == "object" or "properties" in schema:
        if schema.get("additionalProperties", False) is not False:
            raise ValueError("strict 工具不支持开放 additionalProperties")
        required = set(schema.get("required", []))
        props = {}
        for name, item in schema.get("properties", {}).items():
            converted = strict_tool_schema(item, root)
            if name not in required and not validator.evolve(schema=item).is_valid(None):
                converted = {"anyOf": [converted, {"type": "null"}]}
            props[name] = converted
        schema.update(properties=props, required=list(props), additionalProperties=False)
    return schema


def _resolve(schema, root):
    """跟随本地 $ref（#/...）；循环或外部引用原样返回。"""
    seen = set()
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str) and schema["$ref"].startswith("#"):
        ref = schema["$ref"]
        if ref in seen:
            return {}
        seen.add(ref)
        target = root
        for token in ref[1:].lstrip("/").split("/") if ref != "#" else []:
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or token not in target:
                return schema
            target = target[token]
        # 2020-12 允许 $ref 旁边还有其他约束；两部分都要参与还原。
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        schema = {"allOf": [target, siblings]} if siblings else target
    return schema


def restore_optional_arguments(value, schema, root=None):
    """Remove only nulls introduced by strict_tool_schema, leaving genuine nullable data.

    strict_tool_schema also rewrites objects reached through $defs and anyOf/oneOf/allOf,
    so restoration follows the same paths against the original schema.
    """
    root = schema if root is None else root
    schema = _resolve(schema, root)
    if not isinstance(schema, dict):
        return value
    validator = Draft202012Validator(root if isinstance(root, dict) else {})

    def accepts(candidate, subschema):
        return validator.evolve(schema=subschema).is_valid(candidate)

    for branch in schema.get("allOf", []):
        value = restore_optional_arguments(value, branch, root)
    for key in ("anyOf", "oneOf"):
        for branch in schema.get(key, []):
            # 选第一个在还原后满足原 schema 的分支；都不满足时保持原值，交给工具校验报错。
            candidate = restore_optional_arguments(value, branch, root)
            if accepts(candidate, branch):
                value = candidate
                break
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        return {name: restore_optional_arguments(item, properties.get(name, {}), root)
                for name, item in value.items()
                if not (item is None and name in properties and name not in required and
                        not accepts(None, properties[name]))}
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [restore_optional_arguments(item, schema["items"], root) for item in value]
    return value
