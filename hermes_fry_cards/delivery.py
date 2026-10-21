"""单次投递解析结果；显式标识只匹配自身别名。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .streaming.session import CardSession


class DeliveryKind(StrEnum):
    WRITABLE = "writable"
    CLOSED = "closed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeliveryResolution:
    kind: DeliveryKind
    session: CardSession | None = None

    @property
    def consumed(self) -> bool:
        return self.kind == DeliveryKind.CLOSED
