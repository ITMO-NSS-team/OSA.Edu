from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RunOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    presentationType: Literal['scientific', 'industrial', 'collaborative', 'technological'] = 'scientific'
    language: Literal['ru', 'en'] = 'ru'
    criteria: list[str] = Field(default_factory=list, max_length=60)
