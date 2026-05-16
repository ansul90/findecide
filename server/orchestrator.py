"""
FinDecide Orchestrator
----------------------
Manages the multi-turn reasoning loop:
  LLM → Pydantic validate → execute tool (if needed) → feed result back → loop

Supported providers (set LLM_PROVIDER in .env):
  gemini  — Google Gemini via google-genai SDK  (default)
  ollama  — Local Ollama via OpenAI-compatible REST API

Usage:
    from server.orchestrator import run_decision

    result = asyncio.run(run_decision("Should I buy or lease a $30k car?"))
    result = asyncio.run(run_decision("...", provider="ollama"))
"""

from __future__ import annotations

import json
import os
import asyncio
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import ValidationError

load_dotenv()

from server.schemas import (
    parse_llm_response,
    FunctionCall,
    FinalAnswer,
    LLMResponse,
)
from server.mcp_tools.server import (
    _loan_payment,
    _lease_total,
    _future_value,
    _npv,
    _break_even,
    _tax_savings,
    _sanity_check,
)

# ──────────────────────────────────────────────────────────────
# Load the system prompt
# ──────────────────────────────────────────────────────────────
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text()

# ──────────────────────────────────────────────────────────────
# Model / provider config  (overridable via .env)
# ──────────────────────────────────────────────────────────────
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:26b-a4b-it-q4_K_M")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EXTERNAL_BASE_URL = os.environ.get("EXTERNAL_BASE_URL", "http://0.0.0.0:8100")
EXTERNAL_PROVIDER = os.environ.get("EXTERNAL_PROVIDER", "")   # gateway provider: groq, cerebras, gemini, etc.
EXTERNAL_MODEL = os.environ.get("EXTERNAL_MODEL", "")         # model override within that provider
DEFAULT_PROVIDER: Literal["gemini", "ollama", "external"] = os.environ.get("LLM_PROVIDER", "gemini")  # type: ignore[assignment]

Provider = Literal["gemini", "ollama", "external"]

# ──────────────────────────────────────────────────────────────
# Local tool dispatch (without running full MCP server)
# ──────────────────────────────────────────────────────────────
TOOL_DISPATCH = {
    "compute_loan_payment": lambda a: _loan_payment(a["principal"], a["annual_rate"], a["term_months"]),
    "compute_lease_total_cost": lambda a: _lease_total(
        a["monthly_payment"], a["term_months"],
        a.get("down_payment", 0), a.get("acquisition_fee", 0), a.get("disposition_fee", 0),
    ),
    "compute_future_value": lambda a: _future_value(
        a["present_value"], a["annual_rate"], a["years"], a.get("compounds_per_year", 12)
    ),
    "compute_npv": lambda a: _npv(a["cashflows"], a["discount_rate_per_period"]),
    "compute_break_even": lambda a: _break_even(
        a["upfront_cost_a"], a["monthly_cost_a"],
        a["upfront_cost_b"], a["monthly_cost_b"], a.get("max_months", 360),
    ),
    "compute_tax_savings": lambda a: _tax_savings(a["deduction_amount"], a["marginal_tax_rate"]),
    "sanity_check": lambda a: _sanity_check(a["value"], a["expected_min"], a["expected_max"], a["label"]),
}

MAX_TURNS = 20  # safety limit


def execute_tool(tool_name: str, arguments: dict) -> dict:
    """Execute a tool locally and return its result dict."""
    if tool_name not in TOOL_DISPATCH:
        return {"error": f"Tool '{tool_name}' not found. Available: {list(TOOL_DISPATCH.keys())}"}
    try:
        return TOOL_DISPATCH[tool_name](arguments)
    except Exception as e:
        return {"error": str(e), "tool": tool_name}


# ──────────────────────────────────────────────────────────────
# Provider backends
# ──────────────────────────────────────────────────────────────

def _call_gemini(history: list[types.Content], user_text: str) -> str:
    """Single Gemini turn. Returns the raw assistant text."""
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=history + [types.Content(role="user", parts=[types.Part(text=user_text)])],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            max_output_tokens=2048,
        ),
    )
    return response.text.strip() if response.text else ""


def _call_ollama(messages: list[dict], user_text: str) -> str:
    """
    Single Ollama turn via the OpenAI-compatible /api/chat endpoint.
    `messages` is the accumulated chat history (role/content dicts).
    Returns the raw assistant text.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
                    + messages
                    + [{"role": "user", "content": user_text}],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 2048},
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()


def _call_external(messages: list[dict], user_text: str) -> str:
    """
    Single turn via the LLM Gateway V2 at EXTERNAL_BASE_URL (default: http://0.0.0.0:8100).
    Uses /v1/chat with system prompt in a dedicated field and returns response["text"].
    `messages` is the accumulated chat history (role/content dicts).
    Returns the raw assistant text.
    """
    payload: dict = {
        "system": SYSTEM_PROMPT,
        "messages": messages + [{"role": "user", "content": user_text}],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 4096,
        "reasoning": "off",  # prevent thinking tokens from consuming the output budget
    }
    if EXTERNAL_PROVIDER:
        payload["provider"] = EXTERNAL_PROVIDER
    if EXTERNAL_MODEL:
        payload["model"] = EXTERNAL_MODEL
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(f"{EXTERNAL_BASE_URL}/v1/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["text"].strip()


# ──────────────────────────────────────────────────────────────
# Main orchestration loop
# ──────────────────────────────────────────────────────────────

async def run_decision(
    user_question: str,
    on_step=None,
    provider: Provider | None = None,
) -> dict:
    """
    Run a full financial decision reasoning loop.

    Args:
        user_question: The user's financial question.
        on_step:       Optional async callback called with each parsed step.
        provider:      "gemini" or "ollama". Defaults to LLM_PROVIDER env var.

    Returns:
        A dict with 'steps', 'final_answer', and 'provider'.
    """
    active_provider: Provider = provider or DEFAULT_PROVIDER

    # Gemini keeps history as Content objects; Ollama/external use plain dicts.
    gemini_history: list[types.Content] = []
    ollama_history: list[dict] = []
    external_history: list[dict] = []

    steps: list[dict] = []
    final_answer = None
    current_user_text = user_question

    for turn in range(MAX_TURNS):
        # ── Call the active provider ───────────────────────────
        try:
            if active_provider == "gemini":
                raw_text = _call_gemini(gemini_history, current_user_text)
            elif active_provider == "ollama":
                raw_text = _call_ollama(ollama_history, current_user_text)
            else:
                raw_text = _call_external(external_history, current_user_text)
        except Exception as e:
            error_step = {"type": "PROVIDER_ERROR", "provider": active_provider, "error": str(e)}
            steps.append(error_step)
            if on_step:
                await on_step(error_step)
            break

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
            raw_text = raw_text.rsplit("```", 1)[0].strip()

        # Extract only the first complete JSON object; ignore any trailing text
        # (local models often append commentary after the closing brace)
        try:
            data, _ = json.JSONDecoder().raw_decode(raw_text.lstrip())
        except json.JSONDecodeError:
            # Fall back to a full-string parse so the error path below fires
            data = None

        # ── Parse & validate with Pydantic ────────────────────
        try:
            if data is None:
                raise json.JSONDecodeError("No JSON object found", raw_text, 0)
            parsed: LLMResponse = parse_llm_response(data)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            error_step = {
                "type": "PARSE_ERROR",
                "raw": raw_text[:500],
                "error": str(e),
            }
            steps.append(error_step)
            if on_step:
                await on_step(error_step)
            # Append exchange to history so LLM has context when retrying
            _append_history(gemini_history, ollama_history, external_history, active_provider, current_user_text, raw_text)
            current_user_text = (
                f"Your response failed validation: {e}\n"
                "Please respond with valid JSON matching one of the four schemas."
            )
            continue

        step_dict = parsed.model_dump()
        steps.append(step_dict)
        if on_step:
            await on_step(step_dict)

        # ── If FINAL_ANSWER, we're done ───────────────────────
        if isinstance(parsed, FinalAnswer):
            final_answer = step_dict
            break

        # ── Append this exchange to history ───────────────────
        _append_history(gemini_history, ollama_history, external_history, active_provider, current_user_text, raw_text)

        # ── Determine next user message ───────────────────────
        if isinstance(parsed, FunctionCall):
            tool_result = execute_tool(parsed.tool_name, parsed.arguments)
            current_user_text = (
                f"Tool `{parsed.tool_name}` returned:\n```json\n{json.dumps(tool_result)}\n```\n"
                "Continue your reasoning. Remember to emit a SELF_CHECK before FINAL_ANSWER."
            )
        else:
            current_user_text = "Continue. Emit your next step as valid JSON."

    return {"steps": steps, "final_answer": final_answer, "provider": active_provider}


def _append_history(
    gemini_history: list[types.Content],
    ollama_history: list[dict],
    external_history: list[dict],
    provider: Provider,
    user_text: str,
    assistant_text: str,
) -> None:
    """Append a user/assistant exchange to the appropriate history list."""
    if provider == "gemini":
        gemini_history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        gemini_history.append(types.Content(role="model", parts=[types.Part(text=assistant_text)]))
    elif provider == "ollama":
        ollama_history.append({"role": "user", "content": user_text})
        ollama_history.append({"role": "assistant", "content": assistant_text})
    else:
        external_history.append({"role": "user", "content": user_text})
        external_history.append({"role": "assistant", "content": assistant_text})


# ──────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    question = " ".join(sys.argv[1:]) or "Should I pay off my 5% student loan or invest in an index fund returning ~7% annually?"
    print(f"\n🔍 Question: {question}\n{'─' * 60}")
    _model = GEMINI_MODEL if DEFAULT_PROVIDER == "gemini" else (OLLAMA_MODEL if DEFAULT_PROVIDER == "ollama" else EXTERNAL_MODEL)
    print(f"   Provider : {DEFAULT_PROVIDER}  |  Model: {_model}\n")

    async def print_step(step):
        t = step.get("type", "?")
        if t == "REASONING_STEP":
            print(f"[{step['step_number']}] 🧠 REASONING ({step['reasoning_type']}): {step['thought'][:120]}...")
        elif t == "FUNCTION_CALL":
            print(f"[{step['step_number']}] 🔧 TOOL: {step['tool_name']}({step['arguments']})")
        elif t == "SELF_CHECK":
            icon = "✅" if step["passed"] else "⚠️"
            print(f"[{step['step_number']}] {icon} SELF-CHECK: {step['claim_being_checked'][:80]}")
        elif t == "FINAL_ANSWER":
            print(f"\n{'═' * 60}\n✅ RECOMMENDATION: {step['recommendation']}")
            print(f"   Confidence: {step['confidence'].upper()}")
            print(f"   Assumptions: {', '.join(step['key_assumptions'][:2])}")
        elif t in ("PARSE_ERROR", "PROVIDER_ERROR"):
            print(f"   ❌ {t}: {step['error'][:80]}")

    result = asyncio.run(run_decision(question, on_step=print_step))

    if result["final_answer"]:
        fa = result["final_answer"]
        print(f"\n{'─' * 60}")
        print("SCENARIOS:")
        for s in fa.get("scenarios_compared", []):
            print(f"  • {s['name']}: ${s['net_cost']:,.2f} — {s['notes']}")
        print(f"\nFALLBACK ADVICE: {fa.get('fallback_advice', '')}")
