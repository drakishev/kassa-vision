"""Public API contract from section 10 of the supplied case document."""
import base64
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceFrame(StrictModel):
    timestamp_sec: float = Field(ge=0, allow_inf_nan=False)
    image_base64: str

    @model_validator(mode="after")
    def jpeg(self):
        raw = base64.b64decode(self.image_base64, validate=True)
        if not raw.startswith(b"\xff\xd8"):
            raise ValueError("Evidence must be a base64 JPEG")
        return self


class Violation(StrictModel):
    type: str = Field(min_length=1)
    timecode_sec: float = Field(ge=0, allow_inf_nan=False)
    description: str = Field(min_length=1)
    evidence_frames: list[EvidenceFrame] = Field(min_length=1, max_length=3)


class ProcessingJob(StrictModel):
    job_id: str
    status: Literal["processing"] = "processing"


class DoneJob(StrictModel):
    job_id: str
    status: Literal["done"] = "done"
    verdict: Literal["violation", "no_violation"]
    violations: list[Violation]
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_verdict(self):
        if (self.verdict == "violation") != bool(self.violations):
            raise ValueError("verdict must agree with violations")
        return self


class FailedJob(StrictModel):
    job_id: str
    status: Literal["failed"] = "failed"
    error: str


class APIError(StrictModel):
    error: dict[str, str]
