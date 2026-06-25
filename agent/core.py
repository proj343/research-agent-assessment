"""ReAct agent core — Reason + Act loop with multi-step planning."""

import logging
import re
import time
from dataclasses import dataclass, field

from .guardrails import GuardrailError, validate
from .pii import scrub
from .tools.base import BaseTool, ToolResult
from .tracer import Tracer

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a financial research agent for a banking startup. Your job is to answer questions \
about banking, finance, economics, regulations, and related academic research by searching \
authoritative sources.

Available tools:
{tool_descriptions}

Use the following EXACT format for EVERY response until you have a final answer:

Thought: [your reasoning about what information you need and which tool to use next]
Action: [exact tool name from the list above]
Action Input: [the search query to pass to the tool — be specific]

When you have gathered sufficient information to answer the question, use this format instead:

Thought: [final reasoning — summarize what you found and note any gaps]
Final Answer: [comprehensive answer that:
  1. Directly addresses the question
  2. Cites sources in brackets, e.g. [Wikipedia: Article Title] or [arXiv: paper_id] or [FRED: Series Name (ID)]
  3. Clearly distinguishes retrieved facts from your own synthesis/inference
  4. If the question is out of scope (not about finance/economics/banking/related research),
     explains why and what kinds of questions you CAN answer]

Rules:
- You MUST call at least one tool before giving a Final Answer on any in-scope finance/economics/research question
- Do NOT repeat a tool call with the same query
- For multi-source questions (e.g. "explain X AND find papers on X"), use MULTIPLE tools before synthesizing
- Maximum {max_steps} tool calls per question
- Always cite specific sources with titles/IDs, not just "according to Wikipedia"
- Be honest about uncertainty and gaps in your retrieved information
- NEVER answer from memory alone for in-scope questions — always retrieve and cite sources

NPI policy:
- This system is for PUBLIC research only. Do NOT process questions that appear to contain \
nonpublic personal information (NPI) about specific customers — e.g. named individuals paired \
with account balances, transaction histories, credit scores, or loan details.
- If a question contains what looks like customer-specific financial data, respond immediately with:
  Thought: This question appears to contain nonpublic customer information (NPI), which this \
system is not authorized to process.
  Final Answer: I cannot process this request. It appears to contain nonpublic personal \
information (NPI) such as individual customer financial data. This system is authorized for \
public financial research only. Please remove any customer-specific data and rephrase as a \
general research question.
"""


@dataclass
class AgentStep:
    """One iteration of the Thought → Action → Observation loop."""

    step_num: int
    thought: str = ""
    action: str = ""
    action_input: str = ""
    observation: str = ""
    duration_ms: float = 0.0


@dataclass
class AgentResponse:
    """Final output of a single agent run, including all intermediate steps."""

    question: str
    answer: str
    sources: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    total_duration_ms: float = 0.0
    tools_used: list = field(default_factory=list)
    success: bool = True


class ResearchAgent:
    """ReAct-style agent that iterates Thought/Action/Observation until it can answer."""

    MAX_STEPS = 8

    def __init__(
        self,
        tools: list[BaseTool],
        llm,
        tracer: Tracer | None = None,
        max_history: int = 3,
    ):
        self.tools = {t.name: t for t in tools}
        self.llm = llm
        self.tracer = tracer
        self.max_history = max_history
        self.history: list[tuple[str, str]] = []
        self._tool_cache: dict[str, ToolResult] = {}

    def clear_history(self) -> None:
        """Reset conversation history (start a new session)."""
        self.history = []

    def clear_cache(self) -> None:
        """Clear the in-session tool result cache."""
        self._tool_cache = {}

    def run(self, question: str) -> AgentResponse:
        """Execute the ReAct loop for a question and return the final answer with sources."""
        question = scrub(question)
        try:
            validate(question)
        except GuardrailError as e:
            return AgentResponse(question=question, answer=str(e), success=False)
        start = time.time()
        steps: list[AgentStep] = []
        all_sources: list[dict] = []
        used_queries: set[str] = set()
        tools_used: list[str] = []

        tool_descriptions = "\n".join(
            f"  - {name}: {tool.description}" for name, tool in self.tools.items()
        )
        system = SYSTEM_PROMPT.format(
            tool_descriptions=tool_descriptions,
            max_steps=self.MAX_STEPS,
        )

        history_context = ""
        if self.history:
            recent = self.history[-self.max_history :]
            pairs = "\n\n".join(f"Q: {q}\nA: {a}" for q, a in recent)
            history_context = f"Previous questions in this session:\n{pairs}\n\n---\n\n"

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"{history_context}Question: {question}\n\n"
                    "Start by writing a Thought and then an Action to search for relevant information. "
                    "Do NOT write a Final Answer yet — you must retrieve information from at least one tool first."
                ),
            },
        ]

        final_answer: str | None = None

        for step_num in range(1, self.MAX_STEPS + 1):
            step_start = time.time()
            response_text = self.llm.complete(messages, temperature=0.1, max_tokens=2048)
            logger.debug(f"Step {step_num} LLM output:\n{response_text[:500]}")

            parsed = self._parse(response_text)
            step = AgentStep(step_num=step_num)

            if parsed["type"] == "final":
                step.thought = parsed.get("thought", "")
                final_answer = parsed["answer"]
                steps.append(step)
                break

            elif parsed["type"] == "action":
                step.thought = parsed.get("thought", "")
                step.action = parsed["tool"]
                step.action_input = parsed["query"]

                dedup_key = f"{step.action}::{step.action_input.lower().strip()}"
                if dedup_key in used_queries:
                    observation = (
                        "You already made this exact search. Try a different query or "
                        "use a different tool to gather complementary information."
                    )
                else:
                    used_queries.add(dedup_key)
                    tool = self.tools.get(step.action)
                    if tool:
                        if step.action not in tools_used:
                            tools_used.append(step.action)
                        if dedup_key in self._tool_cache:
                            result = self._tool_cache[dedup_key]
                        else:
                            try:
                                result = tool.run(step.action_input)
                            except Exception as exc:
                                result = ToolResult(
                                    content=f"{step.action} failed after retries: {exc}",
                                    sources=[],
                                    success=False,
                                    error=str(exc),
                                )
                            if result.success:
                                self._tool_cache[dedup_key] = result
                        observation = result.content
                        if result.success:
                            all_sources.extend(result.sources)
                        if result.error:
                            logger.warning(f"Tool {step.action} error: {result.error}")
                    else:
                        available = ", ".join(self.tools.keys())
                        observation = f"Unknown tool '{step.action}'. Available tools: {available}"

                step.observation = observation
                step.duration_ms = (time.time() - step_start) * 1000
                steps.append(step)

                messages.append({"role": "assistant", "content": response_text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Observation: {observation[:3000]}\n\n"
                            "Continue. If you have enough information, write your Final Answer. "
                            "Otherwise, continue with another Thought/Action/Action Input."
                        ),
                    }
                )

            else:
                # Malformed output — nudge back on track
                messages.append({"role": "assistant", "content": response_text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Please use the exact format:\n"
                            "Thought: ...\nAction: ...\nAction Input: ...\n\n"
                            "OR if ready:\nThought: ...\nFinal Answer: ..."
                        ),
                    }
                )

        if final_answer is None:
            # Force synthesis after max steps
            messages.append(
                {
                    "role": "user",
                    "content": "You've reached the step limit. Provide your Final Answer now based on everything gathered.",
                }
            )
            response_text = self.llm.complete(messages, temperature=0.1, max_tokens=2048)
            parsed = self._parse(response_text)
            final_answer = parsed.get("answer") or response_text

        response = AgentResponse(
            question=question,
            answer=final_answer,
            sources=self._dedupe(all_sources),
            steps=steps,
            total_duration_ms=(time.time() - start) * 1000,
            tools_used=tools_used,
            success=True,
        )

        self.history.append((question, final_answer))

        if self.tracer:
            try:
                self.tracer.save(response)
            except Exception as e:
                logger.warning(f"Trace save failed: {e}")

        return response

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse(self, text: str) -> dict:
        """Parse LLM output into a typed dict with keys ``type``, ``thought``, and either ``answer`` or ``tool``/``query``."""
        # Final Answer takes priority
        final_m = re.search(r"Final Answer\s*:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
        if final_m:
            thought_m = re.search(
                r"Thought\s*:\s*(.+?)(?=Final Answer)", text, re.DOTALL | re.IGNORECASE
            )
            return {
                "type": "final",
                "thought": thought_m.group(1).strip() if thought_m else "",
                "answer": final_m.group(1).strip(),
            }

        action_m = re.search(r"Action\s*:\s*(\S+)", text, re.IGNORECASE)
        input_m = re.search(
            r"Action Input\s*:\s*(.+?)(?=\nObservation\s*:|$)", text, re.DOTALL | re.IGNORECASE
        )
        thought_m = re.search(
            r"Thought\s*:\s*(.+?)(?=Action\s*:|$)", text, re.DOTALL | re.IGNORECASE
        )

        if action_m and input_m:
            return {
                "type": "action",
                "thought": thought_m.group(1).strip() if thought_m else "",
                "tool": action_m.group(1).strip(),
                "query": input_m.group(1).strip(),
            }

        return {"type": "unknown", "text": text}

    def _dedupe(self, sources: list[dict]) -> list[dict]:
        seen: set[str] = set()
        out = []
        for s in sources:
            key = s.get("url") or s.get("title", "")
            if key not in seen:
                seen.add(key)
                out.append(s)
        return out
