"""协议练习共用的连接配置、加法函数与显式离线 HTTP 夹具。"""
import json

import httpx


PARAMETERS = {"type": "object", "properties": {
    "a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"], "additionalProperties": False}


def post(client, config, path, headers, body):
    response = client.post(config.base_url.rstrip("/") + path,
                           headers=headers, json=body, timeout=config.timeout)
    response.raise_for_status()
    return response.json()


def add(name, arguments):
    if name != "add":
        raise ValueError(f"练习只提供 add 工具，收到 {name}")
    return str(arguments["a"] + arguments["b"])


def run_wire_demo(args, run):
    if args.offline:
        from minicode.providers import ModelConfig
        from minicode.providers.demo import payload

        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=payload(args.api, len(requests) == 1))

        config = ModelConfig(args.api, "fixture-model", "https://fixture.invalid/v1", "fixture-key")
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            return run(client, config)
    from lessons.runtime import open_model

    with open_model(args, []) as model:
        return run(model.client, model.config)
