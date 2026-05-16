# FinDecide — Financial Decision Engine

> A multi-step reasoning agent for personal finance decisions.  
> Built for [The School of AI](https://theschoolofai.in) assignment on structured LLM reasoning.

**[▶ YouTube Demo](#demo)** | **[Prompt Evaluation](#prompt-rubric-evaluation)** | **[Architecture](#architecture)**

---

## What it does

FinDecide takes a personal finance question and reasons through it step-by-step — explicitly, with all math offloaded to tools, and with mandatory self-verification before giving a recommendation.

Example questions:

- "Should I lease or buy a $32,000 car?"
- "Pay off my 5% student loan or invest in index funds?"
- "Should I refinance my 7.5% mortgage to 6.2%?"
- "Rent vs buy a $450k home over 5 years?"

Every response is structured, validated by Pydantic, and streamed live to the web UI.

---

## The Assignment Prompt

The qualifying prompt is in `[prompts/system_prompt.md](prompts/system_prompt.md)`.

It was evaluated against the rubric using Claude claude-opus-4-5 as the evaluator.

### Prompt Rubric Evaluation

The prompt was submitted to Claude with the evaluator instructions:

> "You are a Prompt Evaluation Assistant. Review this prompt and assess how well it supports structured, step-by-step reasoning in an LLM."

**Result:** All 9 criteria passed ✅

```json
{
  "explicit_reasoning": true,
  "structured_output": true,
  "tool_separation": true,
  "conversation_loop": true,
  "instructional_framing": true,
  "internal_self_checks": true,
  "reasoning_type_awareness": true,
  "fallbacks": true,
  "overall_clarity": "Excellent. The prompt defines 4 strict JSON schemas, 7 numbered rules, a complete worked example with 6 turns, and explicit fallback/uncertainty handling. Mental-math prohibition eliminates a major hallucination vector. Reasoning-type tagging on every step makes the chain-of-thought auditable. Self-check is mandatory (not optional), and fallback_advice is a required field in FINAL_ANSWER."
}
```

Full evaluation: `[prompts/rubric_evaluation.json](prompts/rubric_evaluation.json)`

---

## How the prompt qualifies the rubric


| Criterion                    | How satisfied                                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Explicit reasoning**       | Rule 1 mandates ≥2 `REASONING_STEP` blocks before any tool call                                               |
| **Structured output**        | 4 strict JSON schemas (`REASONING_STEP`, `FUNCTION_CALL`, `SELF_CHECK`, `FINAL_ANSWER`) validated by Pydantic |
| **Tool separation**          | `REASONING_STEP` and `FUNCTION_CALL` are distinct schemas; mental math explicitly banned in Rule 2            |
| **Conversation loop**        | Rule 7 addresses multi-turn; `step_number` is monotonically increasing; tool results injected as context      |
| **Instructional framing**    | Full 6-turn worked example with exact JSON for all 4 schemas                                                  |
| **Internal self-checks**     | `SELF_CHECK` is a first-class response type; Rule 4 makes it **mandatory** before `FINAL_ANSWER`              |
| **Reasoning type awareness** | `reasoning_type` is a required field with 7 defined literals on every reasoning step                          |
| **Fallbacks**                | Rule 6 specifies tool-failure handling; `fallback_advice` is a **required field** in `FINAL_ANSWER`           |
| **Overall clarity**          | Self-contained prompt; zero ambiguity; schema enforcement reduces hallucination and drift                     |


---

## Architecture

```
┌─────────────────────────────────────────┐
│           Web UI (index.html)           │
│  Live streaming reasoning trace         │
│  Dark theme, step-by-step animation     │
└──────────────────┬──────────────────────┘
                   │ POST /decide/stream (SSE)
                   ▼
┌─────────────────────────────────────────┐
│     FastAPI Server (server/api.py)      │
│  Streaming SSE endpoint                 │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│   Orchestrator (server/orchestrator.py) │
│  Conversation loop (up to 20 turns)     │
│  Pydantic validation on every response  │
│  Tool dispatch on FUNCTION_CALL steps   │
└──────┬───────────────────────┬──────────┘
       │                       │
       ▼                       ▼
┌─────────────┐    ┌───────────────────────┐
│  Claude API │    │  MCP Tools            │
│  (LLM)      │    │  compute_loan_payment │
└─────────────┘    │  compute_lease_total  │
                   │  compute_future_value │
                   │  compute_npv          │
                   │  compute_break_even   │
                   │  compute_tax_savings  │
                   │  sanity_check         │
                   └───────────────────────┘
```

### Response types (Pydantic-validated)

```
REASONING_STEP  →  The LLM reasons (no math, tagged by type)
FUNCTION_CALL   →  The LLM calls a finance tool
SELF_CHECK      →  The LLM verifies a prior result
FINAL_ANSWER    →  Structured recommendation with scenarios + caveats
```

---

## Project Structure

```
findecide/
├── README.md
├── requirements.txt
├── prompts/
│   ├── system_prompt.md          ← the qualifying prompt
│   └── rubric_evaluation.json    ← Claude's rubric evaluation
├── server/
│   ├── schemas.py                ← Pydantic models for all 4 response types
│   ├── orchestrator.py           ← multi-turn reasoning loop
│   ├── api.py                    ← FastAPI server with SSE streaming
│   └── mcp_tools/
│       └── server.py             ← MCP tool server (7 finance tools)
└── web/
    └── index.html                ← streaming web UI
```

---

## Running locally

This project uses `[uv](https://docs.astral.sh/uv/)` for fast, reproducible dependency management.

### 0. Install uv (if needed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 1. Create a virtual environment and install dependencies

```bash
uv sync
```

This reads `pyproject.toml`, creates a `.venv` automatically, and installs all dependencies in one step.

### 2. Set your API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Or create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Start the server

```bash
uv run uvicorn server.api:app --reload --port 8000
```

### 4. Open the app

Visit `http://localhost:8000` in your browser.

### 5. Run the CLI (optional)

```bash
uv run python -m server.orchestrator "Should I pay off my 5% loan or invest?"
```

> **Tip:** `uv run` automatically uses the project's virtual environment without needing to activate it first.

---

## User questionMCP Tools


| Tool                       | Purpose                                         |
| -------------------------- | ----------------------------------------------- |
| `compute_loan_payment`     | Monthly payment, total paid, total interest     |
| `compute_lease_total_cost` | Full lease outlay (payments + fees + down)      |
| `compute_future_value`     | Compound growth / opportunity cost              |
| `compute_npv`              | Net Present Value of cash flow series           |
| `compute_break_even`       | How many months until Option A beats Option B   |
| `compute_tax_savings`      | Annual savings from a deductible expense        |
| `sanity_check`             | Verify a computed value is in an expected range |


---

## Example Reasoning Trace

For "Should I pay off my 5% student loan or invest in the S&P 500?":

```
[1] 🧠 REASONING (decomposition)
    Sub-problems: after-tax loan cost, expected investment return,
    opportunity cost of each dollar, risk difference...

[2] 🧠 REASONING (assumption)
    Assuming: 7% S&P average return, 22% marginal tax rate,
    effective loan cost = 5% × (1 - 0) = 5% (not deductible above income limit)...

[3] 🔧 TOOL: compute_future_value({present_value: 10000, annual_rate: 0.07, years: 5})
    → { future_value: 14025.52, gain: 4025.52 }

[4] 🔧 TOOL: compute_future_value({present_value: 10000, annual_rate: 0.05, years: 5})
    → { future_value: 12762.82, gain: 2762.82 }

[5] ✅ SELF-CHECK (order_of_magnitude)
    7% investment beats 5% loan cost — difference of ~$1,262 per $10k plausible.

[6] ⚖️ FINAL_ANSWER (confidence: medium)
    Invest in index funds: expected gain ~$4,025 vs ~$2,762 in loan interest saved.
    Caveat: market returns not guaranteed; loan payoff is risk-free.
```

---

## Demo

🎬 **[YouTube Demo](https://youtube.com/YOUR_LINK_HERE)**

The demo shows:

1. The qualifying prompt and rubric evaluation JSON
2. A live multi-step reasoning trace for "Lease vs Buy"
3. Tool calls executing and feeding results back
4. A mandatory self-check before the final recommendation
5. Pydantic validation rejecting a malformed response (fallback handling)

---

## Assignment checklist

- Prompt evaluated/qualified by Claude (all 9 criteria pass)
- Non-trivial, multi-step tool: financial decision engine (not a summarizer)
- Uses MCP tools (7 finance calculation tools)
- Uses Pydantic (all 4 response types validated)
- Web app with live streaming UI
- README with prompt + rubric evaluation
- YouTube demo link

