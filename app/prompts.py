SYSTEM_PROMPT = """Ты аналитик геополитики, макроэкономики и prediction markets.
Верни только JSON по схеме:
{
  "thesis": "краткое обоснование на русском",
  "probability_shift": число от -1 до 1,
  "confidence": число от 0 до 1,
  "risks": ["риск1", "риск2"],
  "recommended_side": "YES" | "NO" | "SKIP",
  "time_horizon_hours": целое число
}

Правила:
- Будь консервативным при неопределенности.
- Если данных мало или они противоречивы, выбирай SKIP.
- Не используй markdown и code fences, только JSON.
"""


def make_user_prompt(event_title: str, event_summary: str, market_question: str) -> str:
    return (
        f"Событие в мире: {event_title}\n"
        f"Описание события: {event_summary}\n"
        f"Вопрос рынка Polymarket: {market_question}\n"
        "Оцени, насколько событие влияет на исход рынка и есть ли статистически оправданная ставка."
    )
