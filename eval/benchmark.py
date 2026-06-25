#!/usr/bin/env python3
"""
Evaluation harness — runs the agent against 15 benchmark questions
and scores answers on coverage, citation quality, and refusal accuracy.

Usage:
    python eval/benchmark.py
    python eval/benchmark.py --questions eval/questions.json --output traces/benchmark_results.json
    python eval/benchmark.py --ids 1,2,3   # run specific questions only
"""

import json
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from agent.llm import create_llm
from agent.tools.wikipedia import WikipediaTool
from agent.tools.arxiv import ArxivTool
from agent.tools.fred import FREDTool
from agent.core import ResearchAgent
from agent.tracer import Tracer


def score_response(answer: str, tools_used: list[str], criteria: dict) -> dict:
    answer_lower = answer.lower()

    # Required term coverage
    required = criteria.get("required_terms", [])
    found = [t for t in required if t.lower() in answer_lower]
    term_coverage = len(found) / len(required) if required else 1.0

    # Citation presence
    citation_markers = ["[wikipedia", "[arxiv", "[fred", "http", "doi.org", "arxiv.org"]
    has_citation = any(m in answer_lower for m in citation_markers)
    citation_score = 1.0 if has_citation or not criteria.get("must_cite", True) else 0.0

    # Refusal accuracy
    should_refuse = criteria.get("should_refuse", False)
    refusal_markers = [
        "out of scope", "cannot", "not relevant", "not designed", "outside",
        "doesn't fall", "speculative", "caveat", "unable to find", "not about finance",
        "outside my scope", "not a research question"
    ]
    did_refuse = any(m in answer_lower for m in refusal_markers)
    if should_refuse:
        refusal_score = 1.0 if did_refuse else 0.0
    else:
        refusal_score = 1.0  # N/A for in-scope questions

    # Tool selection accuracy (soft signal — penalizes only if no tools used on in-scope q)
    if not should_refuse and not tools_used:
        tool_score = 0.3
    else:
        tool_score = 1.0

    # Weighted final score
    score = (
        term_coverage * 0.40
        + citation_score * 0.25
        + refusal_score * 0.25
        + tool_score * 0.10
    )

    return {
        "score": round(score, 3),
        "term_coverage": round(term_coverage, 3),
        "terms_found": found,
        "terms_missing": [t for t in required if t.lower() not in answer_lower],
        "has_citation": has_citation,
        "should_refuse": should_refuse,
        "did_refuse": did_refuse,
        "refusal_correct": (should_refuse == did_refuse),
    }


def run_benchmark(
    questions_file: str | None = None,
    output_file: str | None = None,
    ids: list[int] | None = None,
    verbose: bool = False,
    model: str | None = None,
) -> tuple[list[dict], float]:
    questions_path = questions_file or Path(__file__).parent / "questions.json"
    with open(questions_path) as f:
        benchmark = json.load(f)

    questions = benchmark["questions"]
    if ids:
        questions = [q for q in questions if q["id"] in ids]

    llm = create_llm(model=model)
    tools = [WikipediaTool(), ArxivTool(), FREDTool()]
    tracer = Tracer("traces/eval")
    agent = ResearchAgent(tools=tools, llm=llm, tracer=tracer)

    results = []
    total_score = 0.0

    print(f"\n{'=' * 70}")
    print(f"Research Agent Benchmark — {len(questions)} questions")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 70}\n")

    for i, q in enumerate(questions, 1):
        q_label = f"[{i}/{len(questions)}] Q{q['id']} ({q['type']})"
        print(f"{q_label}: {q['question'][:65]}...")

        try:
            response = agent.run(q["question"])
            eval_result = score_response(response.answer, response.tools_used, q.get("criteria", {}))

            if verbose:
                print(f"  Answer preview: {response.answer[:150]}...")

            result = {
                "id": q["id"],
                "question": q["question"],
                "type": q["type"],
                "tools_used": response.tools_used,
                "num_steps": len(response.steps),
                "duration_s": round(response.total_duration_ms / 1000, 1),
                "answer_preview": response.answer[:300],
                "eval": eval_result,
            }
            total_score += eval_result["score"]
            print(
                f"  Score: {eval_result['score']:.2f} | "
                f"Terms: {eval_result['term_coverage']:.0%} | "
                f"Cited: {'Y' if eval_result['has_citation'] else 'N'} | "
                f"Refusal: {'ok' if eval_result['refusal_correct'] else 'WRONG'} | "
                f"Tools: {', '.join(response.tools_used) or 'none'} | "
                f"{len(response.steps)} steps {response.total_duration_ms/1000:.0f}s"
            )

        except Exception as e:
            print(f"  ERROR: {e}")
            result = {
                "id": q["id"],
                "question": q["question"],
                "type": q["type"],
                "error": str(e),
                "eval": {"score": 0.0},
            }

        results.append(result)

    avg = total_score / len(questions) if questions else 0.0

    print(f"\n{'=' * 70}")
    print(f"Benchmark Complete")
    print(f"Average score  : {avg:.3f} / 1.000")
    print(f"Questions run  : {len(results)}")

    # Per-type breakdown
    by_type: dict[str, list[float]] = {}
    for r in results:
        t = r.get("type", "unknown")
        by_type.setdefault(t, []).append(r["eval"]["score"])
    print("\nBy question type:")
    for qtype, scores in sorted(by_type.items()):
        print(f"  {qtype:<30} avg={sum(scores)/len(scores):.3f}  (n={len(scores)})")
    print(f"{'=' * 70}\n")

    out_path = output_file or "traces/benchmark_results.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(
            {
                "run_at": datetime.now().isoformat(),
                "avg_score": round(avg, 3),
                "num_questions": len(results),
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"Results saved to {out_path}")

    return results, avg


def main():
    parser = argparse.ArgumentParser(description="Run the research agent benchmark")
    parser.add_argument("--questions", default=None, help="Path to questions JSON")
    parser.add_argument("--output", default=None, help="Path for results JSON")
    parser.add_argument("--ids", default=None, help="Comma-separated question IDs to run")
    parser.add_argument("--model", default=None, help="Override LLM model (e.g. meta-llama/llama-4-scout-17b-16e-instruct)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    run_benchmark(
        questions_file=args.questions,
        output_file=args.output,
        ids=ids,
        verbose=args.verbose,
        model=args.model,
    )


if __name__ == "__main__":
    main()
