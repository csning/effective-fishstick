from .base import BaseAgent, AgentContext, AgentResult


class AnomalyMonitor(BaseAgent):
    name = "anomaly"

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        return AgentResult(success=True, summary="anomaly stub")
