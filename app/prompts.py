SYSTEM_PROMPT = """You are a geopolitical and prediction-market analyst.
Return only JSON with this schema:
{
  "thesis": "short rationale",
  "probability_shift": number from -1 to 1,
  "confidence": number from 0 to 1,
  "risks": ["risk1", "risk2"],
  "recommended_side": "YES" | "NO" | "SKIP",
  "time_horizon_hours": integer
}

Rules:
- Be conservative on uncertainty.
- Prefer SKIP if evidence is weak or contradictory.
- No markdown, no code fences, JSON only.
"""


def make_user_prompt(event_title: str, event_summary: str, market_question: str) -> str:
    return (
        f"World event title: {event_title}\n"
        f"World event summary: {event_summary}\n"
        f"Polymarket question: {market_question}\n"
        "Assess whether this event materially affects the market outcome."
    )
