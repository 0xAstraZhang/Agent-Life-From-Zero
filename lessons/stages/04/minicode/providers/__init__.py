from .base import ModelConfig, ProtocolError
from .chat import ChatCompletionsModel
from .responses import ResponsesModel

MODELS = {"openai-chat": ChatCompletionsModel, "openai-responses": ResponsesModel}

def create_model(config, client=None):
    if config.api not in MODELS:
        raise ValueError(f"本阶段尚未实现协议：{config.api}")
    return MODELS[config.api](config, client)
