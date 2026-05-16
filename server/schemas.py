"""
Pydantic schemas for FinDecide — Financial Decision Engine.
Every LLM response is validated against one of these models.
"""

from __future__ import annotations
from typing import Literal, Union, Any
from pydantic import BaseModel, Field, model_validator


# ─────────────────────────────────────────────
# Step 1: The LLM is reasoning (no math yet)
# ─────────────────────────────────────────────
class ReasoningStep(BaseModel):
    type: Literal["REASONING_STEP"]
    step_number: int = Field(..., ge=1)
    reasoning_type: Literal[
        "decomposition",
        "arithmetic",
        "comparison",
        "assumption",
        "lookup",
        "risk_analysis",
        "logic",
    ]
    thought: str = Field(..., min_length=10)
    next_action: Literal["TOOL_CALL", "SELF_CHECK", "REASONING_STEP", "FINAL_ANSWER"]


# ─────────────────────────────────────────────
# Step 2: The LLM wants to call an MCP tool
# ─────────────────────────────────────────────
class FunctionCall(BaseModel):
    type: Literal["FUNCTION_CALL"]
    step_number: int = Field(..., ge=1)
    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any]
    expected_output_range: str = Field(
        ..., description="LLM's prediction of the result — used in self-check"
    )
    why_this_tool: str = Field(..., min_length=5)


# ─────────────────────────────────────────────
# Step 3: The LLM verifies a prior result
# ─────────────────────────────────────────────
class SelfCheck(BaseModel):
    type: Literal["SELF_CHECK"]
    step_number: int = Field(..., ge=1)
    claim_being_checked: str
    verification_method: Literal[
        "unit_check", "order_of_magnitude", "cross_calc", "assumption_review"
    ]
    passed: bool
    notes: str


# ─────────────────────────────────────────────
# Step 4: Final structured recommendation
# ─────────────────────────────────────────────
class Scenario(BaseModel):
    name: str
    net_cost: float
    notes: str


class FinalAnswer(BaseModel):
    type: Literal["FINAL_ANSWER"]
    recommendation: str = Field(..., min_length=10)
    confidence: Literal["high", "medium", "low"]
    key_assumptions: list[str]
    reasoning_summary: list[str]
    scenarios_compared: list[Scenario]
    caveats: list[str]
    fallback_advice: str

    @model_validator(mode="after")
    def at_least_one_scenario(self) -> "FinalAnswer":
        if not self.scenarios_compared:
            raise ValueError("FINAL_ANSWER must compare at least one scenario.")
        return self


# ─────────────────────────────────────────────
# Union type — parse any LLM turn with this
# ─────────────────────────────────────────────
LLMResponse = Union[ReasoningStep, FunctionCall, SelfCheck, FinalAnswer]


def parse_llm_response(data: dict) -> LLMResponse:
    """
    Dispatch to the right Pydantic model based on the 'type' field.
    Raises ValidationError if the JSON doesn't match the schema.
    """
    response_type = data.get("type")
    dispatch: dict[str, type] = {
        "REASONING_STEP": ReasoningStep,
        "FUNCTION_CALL": FunctionCall,
        "SELF_CHECK": SelfCheck,
        "FINAL_ANSWER": FinalAnswer,
    }
    if response_type not in dispatch:
        raise ValueError(
            f"Unknown response type: '{response_type}'. "
            f"Must be one of: {list(dispatch.keys())}"
        )
    return dispatch[response_type].model_validate(data)
