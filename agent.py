#!/usr/bin/env python3
"""
Research Agent CLI — Banking Research Assistant

Usage:
    python agent.py "What is the Federal Reserve's discount window?"
    python agent.py "Recent ML papers on credit risk" --verbose
    python agent.py "Current US unemployment rate" --verbose
"""

import argparse
import logging
import sys

from dotenv import load_dotenv

from agent.core import ResearchAgent
from agent.llm import create_llm
from agent.tools.arxiv import ArxivTool
from agent.tools.fred import FREDTool
from agent.tools.wikipedia import WikipediaTool
from agent.tracer import Tracer


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def print_response(response, verbose: bool) -> None:
    """Pretty-print an ``AgentResponse`` to stdout; includes step trace when ``verbose=True``."""
    sep = "=" * 70

    print(f"\n{sep}")
    print(f"Question: {response.question}")
    print(sep)

    if verbose and response.steps:
        print("\n--- REASONING TRACE ---")
        for step in response.steps:
            print(f"\n[Step {step.step_num}]")
            if step.thought:
                print(f"  Thought: {step.thought[:300]}")
            if step.action:
                print(f"  Action:  {step.action}")
                print(f"  Input:   {step.action_input}")
                if step.observation:
                    preview = step.observation[:300].replace("\n", " ")
                    print(f"  Result:  {preview}...")
        print("\n--- ANSWER ---\n")

    print(response.answer)

    if response.sources:
        print(f"\n{sep}")
        print("Sources:")
        for i, src in enumerate(response.sources, 1):
            src_type = src.get("type", "?").upper()
            title = src.get("title", "Unknown")
            url = src.get("url", "")
            print(f"  [{i}] [{src_type}] {title}")
            if url:
                print(f"       {url}")

    print(f"\n{sep}")
    print(f"Tools used : {', '.join(response.tools_used) or 'none'}")
    print(f"Steps taken: {len(response.steps)}")
    print(f"Time       : {response.total_duration_ms / 1000:.1f}s")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Research Agent — answers finance/banking questions using Wikipedia, arXiv, and FRED.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("question", nargs="?", help="Research question to answer")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show reasoning steps")
    parser.add_argument("--no-trace", action="store_true", help="Disable trace file output")
    parser.add_argument("--provider", default=None, help="LLM provider: groq or ollama")
    parser.add_argument("--model", default=None, help="Override model name")
    parser.add_argument("--traces-dir", default="traces", help="Directory for trace files")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.question:
        parser.print_help()
        print("\nExample:")
        print('  python agent.py "What is the Federal Reserve\'s discount window?"')
        sys.exit(0)

    try:
        llm = create_llm(provider=args.provider, model=args.model)
    except KeyError:
        print("ERROR: GROQ_API_KEY not set.")
        print("  Option 1 (Groq, recommended): Get a free key at https://console.groq.com")
        print("             then: export GROQ_API_KEY=your_key  OR add to .env")
        print("  Option 2 (Ollama, local):     Install Ollama, then:")
        print("             export LLM_PROVIDER=ollama LLM_MODEL=llama3.2:3b")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR initializing LLM: {e}")
        sys.exit(1)

    tools = [WikipediaTool(), ArxivTool(), FREDTool()]
    tracer = None if args.no_trace else Tracer(args.traces_dir)
    agent = ResearchAgent(tools=tools, llm=llm, tracer=tracer)

    response = agent.run(args.question)
    print_response(response, args.verbose)


if __name__ == "__main__":
    main()
