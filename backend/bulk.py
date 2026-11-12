"""Validated, explicit selection snapshots for batch operations."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class BulkIds(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ids: list[Identifier] = Field(min_length=1, max_length=20000)

    @field_validator('ids')
    @classmethod
    def unique_ids(cls, values):
        return list(dict.fromkeys(values))


class PlanBulk(BulkIds):
    ids: list[Identifier] = Field(min_length=1, max_length=500)
    action: Literal['start', 'pause', 'stop', 'delete']


class RunBulk(BulkIds):
    ids: list[Identifier] = Field(min_length=1, max_length=500)
    action: Literal['cancel', 'delete']


class ItemBulk(BulkIds):
    action: Literal['enable', 'archive', 'delete']
