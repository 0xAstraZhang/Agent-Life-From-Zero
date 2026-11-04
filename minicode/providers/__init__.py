"""Select a wire protocol independently from the provider, model and endpoint."""
from .anthropic import AnthropicModel
from .base import ModelConfig, ProtocolError
from .chat import ChatCompletionsModel
from .responses import ResponsesModel


MODELS = {"openai-chat": ChatCompletionsModel, "openai-responses": ResponsesModel,
          "anthropic-messages": AnthropicModel}


def create_model(config: ModelConfig, client=None):
    try:
        model_type = MODELS[config.api]
    except KeyError:
        raise ValueError(f"未知 api：{config.api}；可选：{', '.join(MODELS)}") from None
    return model_type(config, client)
