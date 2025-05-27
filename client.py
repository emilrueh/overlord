import requests, json, contextlib, uuid
from typing import Literal, Generator, Callable
from pydantic import BaseModel


def loads_if_json(data):
    if isinstance(data, str):
        with contextlib.suppress(json.JSONDecodeError):
            data = json.loads(data)
    return data


class PromptArgs(BaseModel):
    name: str
    label: str | None = None
    version: str | None = None


class PromptConfig(BaseModel):
    args: PromptArgs
    placeholders: dict = {}
    project: str


def prepare_prompt(project, args, placeholders=[]) -> PromptConfig:
    prompt_config = PromptConfig(
        args=PromptArgs(
            name=args["name"],
            label=args.get("label"),
            version=args.get("version"),
        ),
        project=project,
    )

    if placeholders:
        prompt_config.placeholders = placeholders

    return prompt_config


class OverlordClientError(Exception):
    "Raised if anything in the overlord client fails."


# ---


class _Client:
    """
    Request client for the Overlord API.

    ### Usage:

    ```python
    # init
    client = Client("http://your-server-url", "your-api-key")

    # check health
    print(client.ping().text)

    # request single event
    response = next(client.request("endpoint", "POST", data={}))
    ```
    """

    def __init__(self, server: str, api_key: str):
        self._server = server
        self._session = requests.Session()

        self._auth(api_key)

    # ---

    def _construct_url(self, endpoint: str = None):
        return f"{self._server.rstrip('/')}/{(endpoint or '').lstrip('/')}"

    def _create_server_error(self, event_data):
        error_type = event_data["type"]
        ErrorClass = type(error_type, (Exception,), {})
        return ErrorClass(event_data["message"])

    @staticmethod
    def _parse_sse(response) -> Generator:
        for line in response.iter_lines():
            if line.startswith(b"event:"):
                event_type = line.split(b":", 1)[1].strip()
                continue

            if line.startswith(b"data:"):
                event_data = line.split(b":", 1)[1].strip()
                try:
                    yield event_type.decode(), json.loads(event_data)
                except json.JSONDecodeError:
                    continue

    def _raise_or_return(self, response):
        for event_type, event_data in self._parse_sse(response):
            if event_type == "error":
                raise self._create_server_error(event_data)
            yield event_data

    # ---

    def _auth(self, api_key: str):
        if not self._server:
            raise OverlordClientError("No server url specified!")

        if "x-api-key" not in self._session.headers or self._session.headers["x-api-key"] != api_key:
            self._session.headers.update({"x-api-key": api_key})

    def ping(self):
        response = self._session.request("GET", self._construct_url())
        response.raise_for_status()
        return response

    # ---

    def request(
        self,
        endpoint: str = None,
        method: Literal["GET", "POST"] = "GET",
        data: dict = None,
    ) -> Generator:

        with self._session.request(
            method,
            self._construct_url(endpoint),
            json=data,
            stream=True,
        ) as response:
            response.raise_for_status()
            yield from self._raise_or_return(response)


# ---


class ChatInput(BaseModel):
    prompt: str | dict | None
    file_urls: list[str] = None
    metadata: dict = None
    tools: dict[str, Callable] = None


class ChatRequest(BaseModel):
    """
    This is mirrored server-side.

    lf_prompt_config:
        - params and placeholders to get a langfuse prompt
        - any chat will need to have a langfuse prompt at the start
        - if a text_prompt is used then the params come from langfuse

    is_new_lf_prompt:
        - required to check whether we send the langfuse prompt to litellm or not again
        - otherwise messages are attached to langfuse via session id

    text_prompt:
        - a simple string can be provided to enable user chatting
        - will be None if a new lf_prompt_config is provided

    message_history:
        - built server-side
        - returned to client

    file_urls:
        - outside of langfuse prompt so files can be provided to text_prompt calls

    metadata:
        - will always contain at least the session_id
        - can contain custom metadata
    """

    lf_prompt_config: PromptConfig
    is_new_lf_prompt: bool
    # ---
    text_prompt: str | None = None
    message_history: list[dict] | None = None
    # ---
    file_urls: list[str] | None = None
    output_schema: str | None = None
    metadata: dict


class _Chat:
    def __init__(self, overlord):
        self.session_id = f"overlord_{uuid.uuid4()}"
        self._overlord = overlord
        self._endpoint = "ai/chat"
        self.tools = None
        #
        self._message_history = None
        self._initial_lf_prompt_config = None
        self._initial_response_schema = None
        self._active_lf_prompt_config = None

    def _handle_lf_prompt_config(self, prompt_data) -> bool:
        prompt_config = prepare_prompt(self._overlord.project, **prompt_data)

        if prompt_config != self._active_lf_prompt_config:
            self._active_lf_prompt_config = prompt_config
            return True
        return False

    def _handle_tool_calls(self, tool_calls):
        if tool_calls:
            if not self.tools:
                raise OverlordClientError("No tools to call were provided!")

            for tool_call in tool_calls:
                tool_function = tool_call["function"]
                function_name = tool_function["name"]

                if function_name not in self.tools:
                    raise OverlordClientError(f"Tool '{function_name}' not in available tools '{', '.join(self.tools)}'")

                function_to_call = self.tools[function_name]
                function_args = json.loads(tool_function["arguments"])
                function_response = function_to_call(**function_args)

                self._message_history.append(
                    dict(
                        tool_call_id=tool_call["id"],
                        role="tool",
                        name=function_name,
                        content=function_response if isinstance(function_response, str) else json.dumps(function_response),
                    )
                )

            # automatically call itself again with the response of the tools using internal active config
            return self.request(ChatInput(prompt=None))

    # ---

    def _prepare_request(self, input_data: ChatInput):
        prompt_data = input_data.prompt
        file_urls = input_data.file_urls
        custom_metadata = input_data.metadata
        self.tools = input_data.tools or self.tools

        is_new_lf_prompt = False
        if isinstance(prompt_data, dict):
            is_new_lf_prompt = self._handle_lf_prompt_config(prompt_data)
        elif not self._active_lf_prompt_config:
            raise OverlordClientError("A chat must be initialized with a Langfuse prompt config!")

        chat_request = ChatRequest(
            lf_prompt_config=self._active_lf_prompt_config or self._initial_lf_prompt_config,
            is_new_lf_prompt=is_new_lf_prompt,
            text_prompt=None if isinstance(prompt_data, dict) else prompt_data,
            message_history=self._message_history,
            file_urls=file_urls,
            output_schema=self._initial_response_schema,
            metadata=dict(session_id=self.session_id, **(dict(custom=custom_metadata) if custom_metadata else {})),
        )

        return chat_request

    def _execute_request(self, request_data):
        try:
            return next(self._overlord.client.request(self._endpoint, "POST", request_data.model_dump()))
        except:
            self._active_lf_prompt_config = None  # reset for clean retry
            raise

    def _handle_response(self, response):
        if not self._message_history:
            self._initial_lf_prompt_config = self._active_lf_prompt_config
            self._initial_response_schema = response["schema"]

        self._message_history = response["messages"]

        tool_response = self._handle_tool_calls(response["tool_calls"])
        if tool_response:
            return tool_response

        reply = response["messages"][-1]["content"]
        return loads_if_json(reply)

    # ---

    def request(self, input_data: ChatInput):
        chat_request = self._prepare_request(input_data)
        response = self._execute_request(chat_request)
        return self._handle_response(response)


# ---


class Overlord:
    """
    Full interface to the Overlord Server.

    ### Usage:

    ```python
    # init
    overlord = Overlord("http://your-server-url", "your-api-key", "your-langfuse-project")

    # health check (optional)
    print(overlord.client.ping().text)

    # 1. runtime persistant chat
    chat = overlord.chat()

    # check session id (optional)
    print(chat.session_id)

    data = overlord.input(...)
    response = chat.request(data)

    # 2. single request
    data = overlord.input(...)
    response = overlord.task(data)
    ```
    """

    def __init__(self, server, api_key, project):
        self.client = _Client(server, api_key)
        self.input = ChatInput
        self.project = project

    def chat(self):
        return _Chat(self)

    def task(self, data):
        chat = self.chat()
        chat.session_id = None
        return chat.request(data)
