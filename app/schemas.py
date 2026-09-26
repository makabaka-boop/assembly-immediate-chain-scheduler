"""Request schemas with strict 422 validation."""

from __future__ import annotations

import pydantic
from pydantic import BaseModel, Field, model_validator

MAX_JOBS = 18
MIN_JOBS = 2
MAX_EDGES = 100


def _ascii_nonempty(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) == 0:
        raise ValueError(f"{name} must be non-empty")
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must contain ASCII characters only")
    return value


class JobIn(BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    id: str
    family: str

    @pydantic.field_validator("id", "family")
    @classmethod
    def _ascii_nonempty(cls, v: str, info: pydantic.ValidationInfo) -> str:
        return _ascii_nonempty(v, info.field_name)


class EdgeIn(BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    before: str
    after: str

    @pydantic.field_validator("before", "after")
    @classmethod
    def _ascii_nonempty(cls, v: str, info: pydantic.ValidationInfo) -> str:
        return _ascii_nonempty(v, info.field_name)


class ScheduleRequest(BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    jobs: list[JobIn] = Field(..., min_length=MIN_JOBS, max_length=MAX_JOBS)
    edges: list[EdgeIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "ScheduleRequest":
        ids = [j.id for j in self.jobs]
        if len(set(ids)) != len(ids):
            raise ValueError("job ids must be unique")

        raw_edges = [(e.before, e.after) for e in self.edges]
        if len(raw_edges) > MAX_EDGES:
            raise ValueError(f"at most {MAX_EDGES} precedence edges are allowed")

        known = set(ids)
        seen: set[tuple[str, str]] = set()
        for before, after in raw_edges:
            if before not in known or after not in known:
                raise ValueError("edge references unknown job id")
            if before == after:
                raise ValueError("self loops are not allowed")
            if (before, after) in seen:
                raise ValueError("duplicate edge")
            seen.add((before, after))
        return self
