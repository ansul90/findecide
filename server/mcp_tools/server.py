"""
FinDecide MCP Tools Server
--------------------------
Exposes financial calculation tools over the Model Context Protocol.
Run with:  python -m server.mcp_tools.server
"""

import math
import json
from typing import Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import mcp.types as types

app = Server("findecide-tools")


# ──────────────────────────────────────────────────────────────
# Tool definitions
# ──────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="compute_loan_payment",
            description=(
                "Calculate monthly payment and total cost of a loan. "
                "Returns monthly_payment, total_paid, total_interest."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "principal": {"type": "number", "description": "Loan amount in dollars"},
                    "annual_rate": {"type": "number", "description": "Annual interest rate as decimal (e.g. 0.06 for 6%)"},
                    "term_months": {"type": "integer", "description": "Loan term in months"},
                },
                "required": ["principal", "annual_rate", "term_months"],
            },
        ),
        Tool(
            name="compute_lease_total_cost",
            description=(
                "Calculate total cost of leasing a vehicle or asset. "
                "Returns total_outlay and monthly breakdown."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "monthly_payment": {"type": "number"},
                    "term_months": {"type": "integer"},
                    "down_payment": {"type": "number", "default": 0},
                    "acquisition_fee": {"type": "number", "default": 0},
                    "disposition_fee": {"type": "number", "default": 0},
                },
                "required": ["monthly_payment", "term_months"],
            },
        ),
        Tool(
            name="compute_future_value",
            description=(
                "Calculate future value of a present sum with compound interest. "
                "Useful for opportunity cost analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "present_value": {"type": "number"},
                    "annual_rate": {"type": "number", "description": "Annual return rate as decimal"},
                    "years": {"type": "number"},
                    "compounds_per_year": {"type": "integer", "default": 12},
                },
                "required": ["present_value", "annual_rate", "years"],
            },
        ),
        Tool(
            name="compute_npv",
            description=(
                "Calculate Net Present Value of a series of cash flows. "
                "Negative cash flows = outflows. First value is at time 0."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cashflows": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "List of cash flows, one per period",
                    },
                    "discount_rate_per_period": {
                        "type": "number",
                        "description": "Discount rate per period as decimal (e.g. 0.005 for 0.5% monthly)",
                    },
                },
                "required": ["cashflows", "discount_rate_per_period"],
            },
        ),
        Tool(
            name="compute_break_even",
            description=(
                "Find break-even period: how many months until Option A "
                "becomes cheaper than Option B given monthly costs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "upfront_cost_a": {"type": "number", "description": "Higher upfront cost option"},
                    "monthly_cost_a": {"type": "number", "description": "Lower monthly cost option A"},
                    "upfront_cost_b": {"type": "number", "description": "Lower upfront cost option"},
                    "monthly_cost_b": {"type": "number", "description": "Higher monthly cost option B"},
                    "max_months": {"type": "integer", "default": 360},
                },
                "required": ["upfront_cost_a", "monthly_cost_a", "upfront_cost_b", "monthly_cost_b"],
            },
        ),
        Tool(
            name="compute_tax_savings",
            description=(
                "Estimate annual tax savings from a deduction (e.g. mortgage interest). "
                "Uses marginal rate approximation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "deduction_amount": {"type": "number"},
                    "marginal_tax_rate": {"type": "number", "description": "Marginal rate as decimal (e.g. 0.22)"},
                },
                "required": ["deduction_amount", "marginal_tax_rate"],
            },
        ),
        Tool(
            name="sanity_check",
            description=(
                "Verify that a value falls within an expected range. "
                "Returns passed=True/False and a message."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "value": {"type": "number"},
                    "expected_min": {"type": "number"},
                    "expected_max": {"type": "number"},
                    "label": {"type": "string"},
                },
                "required": ["value", "expected_min", "expected_max", "label"],
            },
        ),
    ]


# ──────────────────────────────────────────────────────────────
# Tool implementations
# ──────────────────────────────────────────────────────────────

def _loan_payment(principal: float, annual_rate: float, term_months: int) -> dict:
    if annual_rate == 0:
        monthly = principal / term_months
        return {
            "monthly_payment": round(monthly, 2),
            "total_paid": round(monthly * term_months, 2),
            "total_interest": 0.0,
        }
    r = annual_rate / 12
    monthly = principal * (r * (1 + r) ** term_months) / ((1 + r) ** term_months - 1)
    total = monthly * term_months
    return {
        "monthly_payment": round(monthly, 2),
        "total_paid": round(total, 2),
        "total_interest": round(total - principal, 2),
    }


def _lease_total(
    monthly_payment: float,
    term_months: int,
    down_payment: float = 0,
    acquisition_fee: float = 0,
    disposition_fee: float = 0,
) -> dict:
    total = monthly_payment * term_months + down_payment + acquisition_fee + disposition_fee
    return {
        "total_outlay": round(total, 2),
        "monthly_payment": round(monthly_payment, 2),
        "term_months": term_months,
        "upfront_costs": round(down_payment + acquisition_fee, 2),
        "end_costs": round(disposition_fee, 2),
    }


def _future_value(
    present_value: float,
    annual_rate: float,
    years: float,
    compounds_per_year: int = 12,
) -> dict:
    n = compounds_per_year
    fv = present_value * (1 + annual_rate / n) ** (n * years)
    return {
        "future_value": round(fv, 2),
        "gain": round(fv - present_value, 2),
        "effective_annual_rate": round((1 + annual_rate / n) ** n - 1, 6),
    }


def _npv(cashflows: list[float], discount_rate: float) -> dict:
    npv = sum(cf / (1 + discount_rate) ** t for t, cf in enumerate(cashflows))
    return {"npv": round(npv, 2), "periods": len(cashflows)}


def _break_even(
    upfront_a: float,
    monthly_a: float,
    upfront_b: float,
    monthly_b: float,
    max_months: int = 360,
) -> dict:
    if monthly_a >= monthly_b:
        return {"break_even_months": None, "message": "Option A never becomes cheaper on a monthly basis."}
    # upfront_a + monthly_a * t = upfront_b + monthly_b * t
    # t = (upfront_a - upfront_b) / (monthly_b - monthly_a)
    t = (upfront_a - upfront_b) / (monthly_b - monthly_a)
    if t < 0 or t > max_months:
        return {"break_even_months": None, "message": f"Break-even outside {max_months}-month window."}
    return {"break_even_months": round(t, 1), "break_even_years": round(t / 12, 2)}


def _tax_savings(deduction: float, marginal_rate: float) -> dict:
    savings = deduction * marginal_rate
    return {
        "annual_tax_savings": round(savings, 2),
        "monthly_tax_savings": round(savings / 12, 2),
    }


def _sanity_check(value: float, min_: float, max_: float, label: str) -> dict:
    passed = min_ <= value <= max_
    return {
        "passed": passed,
        "value": value,
        "expected_range": [min_, max_],
        "label": label,
        "message": (
            f"✅ {label} ({value}) is within expected range [{min_}, {max_}]."
            if passed
            else f"⚠️ {label} ({value}) is OUTSIDE expected range [{min_}, {max_}]."
        ),
    }


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "compute_loan_payment":
            result = _loan_payment(
                arguments["principal"],
                arguments["annual_rate"],
                arguments["term_months"],
            )
        elif name == "compute_lease_total_cost":
            result = _lease_total(
                arguments["monthly_payment"],
                arguments["term_months"],
                arguments.get("down_payment", 0),
                arguments.get("acquisition_fee", 0),
                arguments.get("disposition_fee", 0),
            )
        elif name == "compute_future_value":
            result = _future_value(
                arguments["present_value"],
                arguments["annual_rate"],
                arguments["years"],
                arguments.get("compounds_per_year", 12),
            )
        elif name == "compute_npv":
            result = _npv(arguments["cashflows"], arguments["discount_rate_per_period"])
        elif name == "compute_break_even":
            result = _break_even(
                arguments["upfront_cost_a"],
                arguments["monthly_cost_a"],
                arguments["upfront_cost_b"],
                arguments["monthly_cost_b"],
                arguments.get("max_months", 360),
            )
        elif name == "compute_tax_savings":
            result = _tax_savings(arguments["deduction_amount"], arguments["marginal_tax_rate"])
        elif name == "sanity_check":
            result = _sanity_check(
                arguments["value"],
                arguments["expected_min"],
                arguments["expected_max"],
                arguments["label"],
            )
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e), "tool": name}

    return [TextContent(type="text", text=json.dumps(result))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
