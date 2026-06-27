from .base import BaseAgent, AgentContext, AgentResult


class DailyReviewer(BaseAgent):
    name = "reviewer"

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        return AgentResult(success=True, summary="reviewer stub")
