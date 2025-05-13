from pydantic import BaseModel
from src.utils.schema_to_model import transform
from src.utils.schema_recursion import break_cycles
from src.services import langfuse, litellm
import json


class ChatRequest(BaseModel):
    lf_prompt_config: langfuse.PromptConfig
    is_new_lf_prompt: bool
    # ---
    text_prompt: str | None = None
    message_history: list[dict] = None
    # ---
    file_urls: list[str] = None
    json_schema: dict | None = None
    metadata: dict


def handle_response_format(json_schema):
    if isinstance(json_schema, dict):
        # break recursion cycles of litellm preventing error
        json_schema = break_cycles(json_schema, json_schema, max_cycles=3)
        response_format: BaseModel = transform(json_schema)
        return response_format

    elif isinstance(json_schema, str):
        # return {"type", "json_object"}
        raise NotImplementedError("Json mode is not supported! Please use structured responses instead.")


def _handle_multimodal_messages(prompt, urls):
    for message in prompt:
        if message["role"] == "user":
            multimodal_messages = [dict(type="text", text=message["content"])]
            for url in urls:
                multimodal_messages.append(dict(type="image_url", image_url=url))
            message["content"] = multimodal_messages

    return prompt


def handle_messages(
    params,
    lf_prompt,
    lf_prompt_config,
    is_new_lf_prompt,
    text_prompt,
    file_urls,
) -> dict[str, list[dict] | dict]:

    if is_new_lf_prompt:
        messages = lf_prompt.compile(**(lf_prompt_config.placeholders or {}))

        # enable prompt management via litellm metadata
        params["metadata"]["prompt"] = lf_prompt

        if isinstance(messages, str):
            messages = [dict(role="user", content=messages)]

    elif isinstance(text_prompt, str):
        messages = [dict(role="user", content=text_prompt)]

    if file_urls:
        messages = _handle_multimodal_messages(messages, file_urls)

    return messages


def filter_system_prompts(messages):
    # only keep the first system prompt if provided
    return [msg for idx, msg in enumerate(messages) if msg.get("role") != "system" or idx == 0]


async def call(data: ChatRequest) -> list[dict]:
    lf_prompt_config = data.lf_prompt_config
    is_new_lf_prompt = data.is_new_lf_prompt
    text_prompt = data.text_prompt
    message_history = data.message_history
    file_urls = data.file_urls
    json_schema = data.json_schema
    metadata = data.metadata

    # --- GET PARAMS FROM LAST LANGFUSE PROMPT PROVIDED

    # get or init client and get prompt object
    lf_prompt = await langfuse.fetch_prompt(lf_prompt_config)

    # extract litellm params
    params = lf_prompt.config.copy()

    # includes session id (and custom metadata if provided)
    params["metadata"] = metadata

    # get json schema from data or pop from params and turn into pydantic for structured response
    schema = json_schema or params.pop("json_schema", None)

    if schema:
        schema_data_model = handle_response_format(schema)
        params["response_format"] = schema_data_model

    # ---

    # build litellm standardized prompt message history
    message_history += handle_messages(
        params,
        lf_prompt,
        lf_prompt_config,
        is_new_lf_prompt,
        text_prompt,
        file_urls,
    )
    message_history = filter_system_prompts(message_history)
    params["messages"] = message_history

    # ---

    response = await litellm.call(**params)
    message_history.append(dict(role="assistant", content=response))

    # must return schema to keep the one from initial lf prompt throughout
    return dict(messages=message_history, schema=schema)
