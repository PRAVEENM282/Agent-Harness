from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MarkPostedIntent(StrictModel):
    action_name: Literal["mark_posted"]
    target_id: StrictInt = Field(gt=0)
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)] = "agent request"


class RetryIntent(StrictModel):
    action_name: Literal["retry"]
    target_id: StrictInt = Field(gt=0)
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class PurgeIntent(StrictModel):
    action_name: Literal["purge"]
    target_id: StrictInt = Field(gt=0)
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


ToolCallIntent = Annotated[
    Union[MarkPostedIntent, RetryIntent, PurgeIntent],
    Field(discriminator="action_name"),
]


class AgentExecuteRequest(StrictModel):
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    target_id: StrictInt = Field(gt=0)


class RestoreRequest(StrictModel):
    action_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class IntentResponse(StrictModel):
    status: str
    message: str
    intent_id: str | None = None
    action_id: str | None = None
    action_type: str
    target_id: int
    result: dict | None = None


def model_dump(model: BaseModel) -> dict:
    return model.model_dump(mode="json")
