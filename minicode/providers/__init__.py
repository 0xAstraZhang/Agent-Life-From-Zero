"""Select a wire protocol independently from the provider, model and endpoint."""
from importlib import import_module

from .base import ModelConfig, ProtocolError


MODELS = {"openai-chat": ("chat", "ChatCompletionsModel"),
          "openai-responses": ("responses", "ResponsesModel"),
          "anthropic-messages": ("anthropic", "AnthropicModel")}


def create_model(config: ModelConfig, client=None):
    try:
        module_name, class_name = MODELS[config.api]
    except KeyError:
        raise ValueError(f"未知 api：{config.api}；可选：{', '.join(MODELS)}") from None
    model_type = getattr(import_module(f".{module_name}", __name__), class_name)
    return model_type(config, client)
