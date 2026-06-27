from dataclasses import dataclass, field

from loguru import logger

from .base import BaseAgent, AgentContext, AgentResult


@dataclass
class Step:
    agent: BaseAgent
    kwargs: dict = field(default_factory=dict)


class Orchestrator:

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        self._agents[agent.name] = agent
        logger.info(f"Registered agent: {agent.name}")

    async def run_pipeline(
        self, steps: list[Step], ctx: AgentContext | None = None
    ) -> list[AgentResult]:
        ctx = ctx or AgentContext()
        results: list[AgentResult] = []
        for step in steps:
            agent = step.agent
            logger.info(f"Running agent: {agent.name}")
            result = await agent.run(ctx, **step.kwargs)
            if not result.success:
                logger.error(f"Agent {agent.name} failed: {result.error}")
            results.append(result)
            ctx.flags.update(result.meta)
        return results
