from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentResult:
    success: bool
    data: Any = None
    summary: str = ""
    meta: dict = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class AgentContext:
    risk_level: int = 3
    holdings: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)


class BaseAgent(ABC):
    name: str = "base"

    @abstractmethod
    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        ...
