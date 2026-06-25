"""Structured tracing — writes one JSON file per run for full observability."""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class Tracer:
    """Writes one JSON file per agent run for offline analysis and debugging."""

    def __init__(self, traces_dir: str = "traces"):
        self.traces_dir = Path(traces_dir)
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    def save(self, response) -> str:
        """Serialize an AgentResponse to a timestamped JSON trace file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = response.question[:40].replace(" ", "_").replace("?", "").replace("/", "-")
        slug = "".join(c for c in slug if c.isalnum() or c in "_-")
        filepath = self.traces_dir / f"{timestamp}_{slug}.json"

        trace = {
            "question": response.question,
            "timestamp": datetime.now().isoformat(),
            "success": response.success,
            "total_duration_ms": round(response.total_duration_ms),
            "tools_used": response.tools_used,
            "num_steps": len(response.steps),
            "sources": response.sources,
            "answer": response.answer,
            "steps": [
                {
                    "step": s.step_num,
                    "thought": s.thought,
                    "action": s.action,
                    "action_input": s.action_input,
                    "observation_length": len(s.observation),
                    "observation_preview": s.observation[:600]
                    + ("..." if len(s.observation) > 600 else ""),
                    "duration_ms": round(s.duration_ms),
                }
                for s in response.steps
            ],
        }

        with open(filepath, "w") as f:
            json.dump(trace, f, indent=2, default=str)

        logger.info(f"Trace saved: {filepath}")
        return str(filepath)
