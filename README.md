# Research Agent Framework

A multi-tool research agent for banking and finance questions. Given a natural language question, the agent plans which tools to call, executes searches across Wikipedia, arXiv, and FRED, and synthesizes a cited answer.

> **Free to run** — the only required credential is a [Groq API key](https://console.groq.com) (no credit card, instant signup). Wikipedia and arXiv need no key at all; FRED is optional and has its own free key.

## Quick Start

```bash
git clone https://github.com/proj343/research-agent-assessment
cd research-agent-assessment
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GROQ_API_KEY (free at https://console.groq.com)
python3 agent.py "What is the Federal Reserve's discount window?"
```

## Setup

**Python**: 3.11+

**Dependencies** (`pip install -r requirements.txt`):
- `groq` — Groq cloud LLM client
- `requests` — HTTP for tool APIs
- `python-dotenv` — `.env` file loading

### LLM Options (both free, no credit card)

| Option | Setup | Speed | Quality |
|--------|-------|-------|---------|
| **Groq** (default) | Sign up at [console.groq.com](https://console.groq.com), copy key to `.env` | Fast (~3–8s/query) | High (70B model) |
| **Ollama** (local) | [Install Ollama](https://ollama.ai), run `ollama pull llama3.2:3b` | Slower on CPU | Good (3–7B model) |

**Using Groq** (recommended):
```bash
# In .env:
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
LLM_MODEL=llama-3.3-70b-versatile   # or llama-3.1-8b-instant for speed
```

**Using Ollama**:
```bash
# Install Ollama: https://ollama.ai
ollama pull llama3.2:3b
# In .env:
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2:3b
```

### FRED API (optional, for economic data questions)

Get a free key at [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) (instant signup, no credit card). Add to `.env`:
```
FRED_API_KEY=your_fred_key
```

Without a FRED key, questions about current economic data (unemployment rate, GDP) fall back to Wikipedia summaries.

## Usage

```bash
# Basic
python3 agent.py "What are the Basel III capital requirements?"

# With verbose reasoning trace
python3 agent.py "How did Fed policy differ between 2008 and COVID-19?" --verbose

# Disable trace file output
python3 agent.py "What is quantitative easing?" --no-trace

# Use specific provider/model
python3 agent.py "Your question" --provider ollama --model llama3.2:3b
```

## Architecture

### Agent Pattern: ReAct (Reason + Act)

The agent uses the [ReAct](https://arxiv.org/abs/2210.03629) pattern — interleaving reasoning (Thought) with tool execution (Action/Observation) until it has enough information to synthesize a final answer.

```
Question → [Thought → Action → Observation] × N → Final Answer
```

**Why ReAct?** It's interpretable (every decision is traced), naturally handles multi-step reasoning (the agent can refine searches based on intermediate results), and is well-suited to the open-ended research task where the number of needed tool calls isn't known in advance.

### Components

```
agent.py                 # CLI entry point
agent/
  core.py                # ReAct agent loop — orchestrates LLM + tools
  llm.py                 # LLM abstraction (Groq / Ollama)
  pii.py                 # PII scrubbing + log filter — redacts sensitive patterns
  tracer.py              # Structured JSON tracing
  tools/
    base.py              # BaseTool interface + retry decorator
    wikipedia.py         # Wikipedia REST API tool
    arxiv.py             # arXiv Atom API tool
    fred.py              # FRED economic data API tool
eval/
  benchmark.py           # Evaluation harness
  questions.json         # 15 benchmark questions with scoring criteria
logs/                    # Rotating log file (agent.log, up to 5 MB × 3 backups)
traces/                  # Auto-generated per-run JSON traces
```

### Tool Interface

Every tool implements the same interface, making them interchangeable and easy to extend:

```python
class BaseTool(ABC):
    name: str         # used in LLM prompts and routing
    description: str  # tells the LLM when to use this tool

    @abstractmethod
    def run(self, query: str) -> ToolResult:
        # Returns: content (str), sources (list[dict]), success (bool)
        pass
```

Adding a new tool is a 3-step plug-in operation:
1. Create `agent/tools/my_tool.py` extending `BaseTool`
2. Add an instance to the `tools` list in `agent.py`
3. No other changes needed — the agent learns available tools from their `name` and `description`

### Session Memory & Caching

`ResearchAgent` persists state across multiple `.run()` calls within the same process:

- **Conversation history**: the last 3 Q&A pairs are prepended as context to each new question, enabling natural follow-up questions ("what about the European equivalent?"). Call `agent.clear_history()` to start a fresh session.
- **Tool result cache**: successful tool calls are cached in-memory keyed by `(tool, query)`. A repeated query within or across turns skips the network call entirely. Call `agent.clear_cache()` to force fresh fetches.

### Multi-Step Reasoning (Tier 2)

The agent maintains state across steps within a single run:
- **Deduplication**: tracks `(tool, query)` pairs; skips redundant calls within a run (the cache extends this across runs)
- **Context accumulation**: each observation is fed back into the conversation, so the LLM reasons over all prior findings when deciding the next action
- **Max steps**: configurable ceiling (default: 8) with forced synthesis if reached

**Example of multi-step improving over single-pass**:

Question 4 (Fed response 2008 vs COVID-19): A single Wikipedia search for "Federal Reserve 2008 crisis" returns the 2008 side well, but misses the COVID response. The agent recognizes this gap in its reasoning step and performs a second search for "Federal Reserve COVID-19 monetary policy", then synthesizes both. Single-pass would produce a one-sided answer.

Question 6 (yield curve inversions + academic papers): The agent first searches Wikipedia for the conceptual explanation, then issues a targeted arXiv search for recent papers — two tools, two steps, complementary results.

### Observability (Tier 2)

**Logs** are written to two sinks simultaneously:
- `logs/agent.log` — always DEBUG and above; rotates at 5 MB, keeps 3 backups
- stderr — WARNING and above by default; DEBUG with `--verbose`

Both sinks pass through `ScrubFilter` (see Privacy below) before anything is written.

**Traces** — every run also produces a structured JSON trace in `traces/`:

```json
{
  "question": "...",
  "timestamp": "2025-...",
  "tools_used": ["wikipedia_search", "arxiv_search"],
  "total_duration_ms": 8423,
  "steps": [
    {
      "step": 1,
      "thought": "I should search Wikipedia first for the conceptual definition",
      "action": "wikipedia_search",
      "action_input": "Basel III capital requirements banks",
      "observation_preview": "Basel III is an international regulatory framework...",
      "duration_ms": 1823
    }
  ],
  "answer": "..."
}
```

A reviewer can open any trace file and understand exactly why the agent made each decision.

### Privacy & Compliance

This system is intended for **public financial research only**. Two layers protect against sensitive data leakage:

**PII scrubbing** (`agent/pii.py`) — applied at two points: to every question before it reaches the LLM or trace files, and to every log record via `ScrubFilter` before it is written to stderr or `logs/agent.log`. Redacts structural patterns with placeholder tags:

| Pattern | Example | Tag |
|---------|---------|-----|
| Social Security Number | `123-45-6789` | `[SSN]` |
| Credit/debit card | `4111 1111 1111 1111` | `[CARD_NUM]` |
| Email address | `user@bank.com` | `[EMAIL]` |
| US phone number | `(800) 555-1234` | `[PHONE]` |
| Labeled account number | `account # 123456789` | `[ACCOUNT_NUM]` |
| API key in URL | `api_key=abc123` | `api_key=[API_KEY]` |
| HTTP auth header | `Authorization: Bearer sk-...` | `Authorization: Bearer [API_KEY]` |

The last two patterns prevent API keys from appearing in logs even when a network exception embeds the full request URL (the primary real-world leak vector).

A `WARNING` log line is emitted whenever redaction occurs, providing an audit trail without storing the sensitive value.

**NPI system prompt guard** — the agent's system prompt instructs it to refuse any question that appears to contain nonpublic personal information (NPI) under Gramm-Leach-Bliley: named individuals paired with account balances, transaction history, credit scores, or loan details. The refusal fires before any tool call, so NPI never reaches the search tools or traces.

> **Note:** NPI detection is contextual and cannot be fully automated. These guardrails are a safety net — access controls and a clear usage policy (no customer data in queries) are the primary line of defense.

### Reliability (Tier 3)

- **Retry with backoff**: all HTTP calls retry up to 3 times with exponential backoff (1.5× factor)
- **Graceful FRED fallback**: if FRED API key is missing, the agent transparently falls back to Wikipedia for economic questions
- **Unknown tool handling**: if the LLM hallucinates a tool name, the agent returns a clear error and lists available tools
- **Parse recovery**: if LLM output doesn't match ReAct format, the agent prompts for correction rather than crashing

## Evaluation

Run the full benchmark (15 questions):

```bash
python3 eval/benchmark.py
```

Run specific questions:

```bash
python3 eval/benchmark.py --ids 1,2,3,7
```

**Scoring** (per question, 0–1):
- **Term coverage** (40%): does the answer contain key expected terms?
- **Citation quality** (25%): does the answer cite sources?
- **Refusal accuracy** (25%): for out-of-scope questions, does the agent appropriately decline?
- **Tool usage** (10%): did the agent use at least one tool for in-scope questions?

## Reference Question Outputs

See [`outputs/reference_outputs.md`](outputs/reference_outputs.md) for full agent outputs on all 8 reference questions.

## Design Decisions & Tradeoffs

**Why Groq over Ollama as default?**
Groq's free tier provides 70B-parameter model quality with low latency (~3–8s per query). Ollama runs locally with no internet dependency, but requires a large model download and runs significantly slower on CPU. Both are free; Groq produces notably better research synthesis.

**Why ReAct over plan-and-execute?**
Plan-and-execute generates a full tool-calling plan upfront before executing. ReAct re-plans after each observation. For research tasks where intermediate results frequently reshape what to search next, ReAct's adaptive approach outperforms a fixed upfront plan. The tradeoff is slightly more LLM calls.

**Why Wikipedia + arXiv + FRED?**
These three tools cover the main question types: definitional/historical (Wikipedia), recent research (arXiv), and current quantitative data (FRED). Wikipedia has no rate limits, arXiv is open-access, and FRED's free key provides access to the world's largest public economic dataset.

**Context window management**: Tool outputs are truncated at 6,000 characters per observation to stay within Groq's context limits while preserving the most relevant content.

## Limitations & What I'd Do With More Time

**Current limitations:**
- No cross-session persistence — history and cache are in-memory only; a process restart starts fresh
- Wikipedia summaries sometimes truncate exactly where the most relevant detail appears
- FRED tool resolves series by keyword matching — more nuanced NLP-based matching would improve data retrieval accuracy
- No streaming output (answer appears all at once after all tool calls complete)
- The agent doesn't rank sources by recency or authority

**With more time I would:**
1. Add a **semantic search layer** (embeddings on tool results) to avoid feeding irrelevant content to the LLM
2. Implement **streaming output** so the user sees progress in real time
3. Build a **web UI** with trace visualization (the JSON traces are already structured for this)
4. Expand the **eval harness** with LLM-based answer grading (not just keyword matching)
5. Add **more tools**: SEC EDGAR for company filings, SSRN for finance working papers, BIS for international banking data

## Prompt Log

See [`prompt-log.md`](prompt-log.md) — all Claude Code interactions are automatically captured via hooks.
