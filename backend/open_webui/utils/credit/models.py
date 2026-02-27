from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import List, Union, Optional


class CompletionTokensDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    reasoning_tokens: Optional[int] = None


class PromptTokensDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    cached_tokens: Optional[int] = None


class CompletionUsage(BaseModel):
    model_config = ConfigDict(extra="allow")
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    prompt_tokens_details: Optional[PromptTokensDetails] = None
    completion_tokens_details: Optional[CompletionTokensDetails] = None
    input_tokens_details: Optional[PromptTokensDetails] = None
    output_tokens_details: Optional[CompletionTokensDetails] = None

    @model_validator(mode="before")
    @classmethod
    def format_input(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        # standard tokens
        prompt_tokens = (
            data.pop("prompt_tokens", 0)
            or data.pop("promptTokenCount", 0)
            or data.pop("input_tokens", 0)
        )
        completion_tokens = (
            data.pop("completion_tokens", 0)
            or data.pop("candidatesTokenCount", 0)
            or data.pop("output_tokens", 0)
        )
        total_tokens = (
            data.pop("total_tokens", 0)
            or data.pop("totalTokenCount", 0)
            or (prompt_tokens + completion_tokens)
        )
        # update data
        data.update(
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
        )
        return data


class ChatCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: Optional[str] = None


class ChoiceDelta(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: Optional[str] = None


class Choice(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: Optional[ChatCompletionMessage] = Field(
        default_factory=lambda: ChatCompletionMessage()
    )
    delta: Optional[ChoiceDelta] = Field(default_factory=lambda: ChoiceDelta())


class ChatCompletion(BaseModel):
    model_config = ConfigDict(extra="allow")
    choices: List[Choice] = Field(default_factory=lambda: [])
    usage: Optional[CompletionUsage] = None


class ChatCompletionChunk(BaseModel):
    model_config = ConfigDict(extra="allow")
    choices: List[Choice] = Field(default_factory=lambda: [])
    usage: Optional[CompletionUsage] = None


class FileFile(BaseModel):
    model_config = ConfigDict(extra="allow")

    file_data: Optional[str] = Field(default="")
    file_id: Optional[str] = Field(default="")
    filename: Optional[str] = Field(default="")


class InputAudio(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: Optional[str] = Field(default="")
    format: Optional[str] = Field(default="")


class ImageURL(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: Optional[str] = Field(default="")
    detail: Optional[str] = Field(default="")


class MessageContent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Optional[str] = Field(default="")
    text: Optional[str] = Field(default="")
    image_url: Optional[ImageURL] = Field(default_factory=lambda: ImageURL())
    input_audio: Optional[InputAudio] = Field(default_factory=lambda: InputAudio())
    file: Optional[FileFile] = Field(default_factory=lambda: FileFile())


class MessageItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Union[str, list[MessageContent]] = Field(default="")
