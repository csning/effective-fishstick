from .base import BaseAgent, AgentContext, AgentResult


class TimingAgent(BaseAgent):
    name = "timing"

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        return AgentResult(success=True, summary="timing stub")
