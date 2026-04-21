SYSTEM_PROMPT = """Ты аналитик крипторынка, макроэкономики и prediction markets.
Верни только JSON по схеме:
{
  "thesis": "краткое обоснование на русском",
  "probability_shift": число от -1 до 1,
  "confidence": число от 0 до 1,
  "risks": ["риск1", "риск2"],
  "recommended_side": "YES" | "NO",
  "time_horizon_hours": целое число
}

Правила:
- Будь консервативным при неопределенности.
- Если данных мало или они противоречивы, выбирай NO и понижай confidence.
- Приоритет №1: рынки и события, связанные с Bitcoin (BTC), особенно движение цены вверх/вниз.
- Если вопрос рынка про рост/падение BTC и новость релевантна, допускается повышенная уверенность.
- Не используй markdown и code fences, только JSON.
"""


def make_user_prompt(
  event_title: str,
  event_summary: str,
  event_url: str,
  market_question: str,
  yes_price: float | None,
  no_price: float | None,
) -> str:
  yes_text = f"{yes_price:.3f}" if yes_price is not None else "N/A"
  no_text = f"{no_price:.3f}" if no_price is not None else "N/A"
    return (
        f"Событие в мире: {event_title}\n"
        f"Описание события: {event_summary}\n"
    f"Ссылка на новость: {event_url}\n"
        f"Вопрос рынка Polymarket: {market_question}\n"
    f"Текущие котировки рынка: YES={yes_text}, NO={no_text}\n"
    "Оцени, насколько событие влияет на исход рынка с учетом котировок и новостного контекста. "
    "Если стоит входить в позицию, выбери сторону YES/NO. Если входить не стоит, выбери NO с низкой confidence."
    )
