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

SYSTEM_PROMPT_BTC = """Ты эксперт по краткосрочной торговле Bitcoin на Polymarket.
Твоя задача: определить, вырастет ли цена BTC в ближайшие 5 минут.

Анализируй:
1. Текущую цену BTC и динамику свечей (открытие/закрытие/объём)
2. Тренд: импульс вверх/вниз по последним свечам
3. Новостной фон: войны, санкции, макро-события, заявления ФРС/ЕЦБ, IPO, хаки
4. Новости гигантов GPU/AI (NVIDIA, Microsoft, Google) — коррелируют с BTC
5. Геополитика: ЧП, ЧС, начало военных действий — обычно давят на BTC
6. Положительные триггеры: одобрения ETF, покупки крупных игроков, лёгкая монетарная политика

Верни только JSON:
{
  "thesis": "краткое обоснование на русском (2-3 предложения)",
  "direction": "UP" | "DOWN",
  "confidence": число от 0 до 1,
  "risks": ["риск1", "риск2"],
  "recommended_side": "YES" | "NO"
}

YES = цена вырастет (ставка на рост)
NO = цена упадёт или останется (ставка на падение)

Правила:
- confidence < 0.6 → recommended_side="NO" (лучше не ставить)
- Если нет явного сигнала — confidence=0.4-0.5, recommended_side по тренду свечей
- Только JSON, без markdown.
"""


def make_btc_prompt(
    current_price: float,
    candles: list[dict],
    news_headlines: list[str],
    market_question: str,
    yes_price: float | None,
    no_price: float | None,
    price_source: str,
    price_source_url: str,
) -> str:
    candle_lines = []
    for c in candles[-10:]:  # последние 10 из 15
        direction = "▲" if c["close"] >= c["open"] else "▼"
        candle_lines.append(
            f"  {direction} O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f} V={c['volume']:.1f}"
        )
    candles_text = "\n".join(candle_lines) if candle_lines else "  нет данных"

    news_text = "\n".join(f"  - {h}" for h in news_headlines[:8]) if news_headlines else "  нет новостей"

    yes_text = f"{yes_price:.3f}" if yes_price is not None else "N/A"
    no_text = f"{no_price:.3f}" if no_price is not None else "N/A"

    return (
        f"Текущая цена BTC/USDT: ${current_price:,.2f}\n\n"
        f"Источник цены: {price_source}\n"
        f"URL источника: {price_source_url}\n"
        "Важно: для BTC Up/Down market резолв идет по Chainlink BTC/USD stream, а не по споту биржи.\n\n"
        f"Последние 1-мин свечи (старые → новые):\n{candles_text}\n\n"
        f"Свежие новости (последние заголовки):\n{news_text}\n\n"
        f"Вопрос рынка Polymarket: {market_question}\n"
        f"Котировки рынка: YES={yes_text} (рост), NO={no_text} (падение)\n\n"
        "Определи направление BTC за следующие 5 минут с учётом всех факторов выше."
    )


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
