from .base import BaseAgent, AgentContext, AgentResult


class PositionAdvisor(BaseAgent):
    name = "position_advisor"

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        return AgentResult(success=True, summary="position_advisor stub")
