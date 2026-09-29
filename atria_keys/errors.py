from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureClass(StrEnum):
    TRANSIENT = "transient"
    FATAL = "fatal"


@dataclass(slots=True)
class ProvisioningError(Exception):
    code: str
    failure_class: FailureClass
    message: str
    stage: str | None = None
    artifact: str | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class TransientProvisioningError(ProvisioningError):
    def __init__(self, code: str, message: str, *, stage: str | None = None):
        super().__init__(code, FailureClass.TRANSIENT, message, stage)


class FatalProvisioningError(ProvisioningError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str | None = None,
        artifact: str | None = None,
    ):
        super().__init__(code, FailureClass.FATAL, message, stage, artifact)

