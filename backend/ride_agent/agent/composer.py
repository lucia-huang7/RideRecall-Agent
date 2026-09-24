import asyncio
import json
from typing import Any, List, Optional

from anthropic import AsyncAnthropic

from ride_agent.agent.llm_utils import extract_text_content
from ride_agent.agent.types import (
    AgentCallResult,
    AgentResponse,
    Request,
)


class ResponseComposer:
    """Response composer."""

    def __init__(self, client: AsyncAnthropic, model: str, skill_manager: Optional[Any] = None):
        self._client = client
        self._model = model
        self._skill_manager = skill_manager

    async def compose(self, req: Request, responses: List[AgentResponse]) -> str:
        return (await self.compose_result(req, responses)).content

    async def compose_result(self, req, responses):
        valid = [r for r in responses if r.success and r.content.strip()]
        if not valid:
            return AgentCallResult(
                ("The analysis could not be completed. No payment action was taken. You can request human review."),
                status="failed",
            )
        status = "success" if len(valid) == len(responses) else "partial"
        if len(valid) == 1:
            return AgentCallResult(valid[0].content, status=status)
        try:
            answer = await self._client.messages.create(
                model=self._model,
                max_tokens=1400,
                temperature=0,
                system=(
                    "Combine the supplied evidence-based role responses in "
                    "English. Preserve source citations and uncertainty. Do not "
                    "introduce facts or claim an action was "
                    "executed."
                ),
                messages=[
                    dict(
                        role="user",
                        content=json.dumps(
                            dict(
                                task="compose",
                                message=req.message,
                                responses=[dict(agent=r.agent_type.value, content=r.content) for r in valid],
                            )
                        ),
                    )
                ],
            )
            text = extract_text_content(answer.content).strip()
            if not text:
                raise ValueError("Empty composition")
            return AgentCallResult(text, status=status)
        except asyncio.CancelledError:
            raise
        except Exception:
            return AgentCallResult(
                "\n\nAdditional findings:\n".join((r.content for r in valid)),
                status="partial",
            )
