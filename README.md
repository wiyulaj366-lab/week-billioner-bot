# week-billioner-bot

24/7 бот для мониторинга Polymarket + мировых событий с приоритетом крипто-новостей.

## Что делает бот

- Собирает мировые новости (RSS) и события Polymarket.
- Приоритизирует крипто-триггеры и макро/геополитику (Украина, Ближний Восток, санкции, ставки ФРС и т.д.).
- Отправляет событие на анализ 3 LLM.
- Формирует сигнал ставки и риск-проверки.
- В ручном режиме отправляет только actionable-сигналы с кнопками:
  - `Отклонить`
  - `Поставить`
- Ведет статистику: победы/поражения, PnL, стартовый vs текущий портфель, ожидающие решения.

## Админ-панель (Telegram, на русском)

Поддерживается приватный доступ по `ADMIN_TELEGRAM_USER_ID`.

Команды:

- `/start` или `/help` — помощь и меню.
- `/panel` — кнопочная панель:
  - `Статистика`
  - `Текущие ставки`
  - `История ставок`
  - `Ожидают решения`
  - `Переключить авто/ручной режим`
- `/status` — статус runtime.
- `/keys` — список ключей.
- `/set KEY VALUE` — изменить настройку без рестарта.
- `/show KEY` — посмотреть значение (секреты маскируются).
- `/mode auto` — автоисполнение.
- `/mode manual` — ручное подтверждение.
- `/settle ID win|loss [pnl_usd]` — вручную закрыть ставку для учета статистики.

## Быстрый запуск

1. Скопируй конфиг:
```bash
cp .env.example .env
```

2. Заполни `.env`:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ADMIN_TELEGRAM_BOT_TOKEN`
- `ADMIN_TELEGRAM_USER_ID`
- `LLM_1_*`, `LLM_2_*`, `LLM_3_*`

3. Запуск:
```bash
docker compose up -d --build
```

4. Проверка:
```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/run-once
```

## Ключевые параметры в `.env`

- `AUTO_EXECUTE` — автоставки (`true`) или ручное подтверждение (`false`).
- `DRY_RUN` — симуляция сделок.
- `MAX_BET_USD`
- `MIN_CONFIDENCE`
- `MAX_DAILY_LOSS_USD`
- `MIN_MARKET_VOLUME`
- `INITIAL_BANKROLL_USD`
- `USER_LANGUAGE` (`ru`/`en`, русский по умолчанию)

## CI/CD

- `CI`: `.github/workflows/ci.yml`
- `Deploy`: `.github/workflows/deploy.yml` (self-hosted runner на сервере)

Деплой после `push` в `main`:

- код синхронизируется в `/opt/week-billioner-bot`
- выполняется `docker compose up -d --build`
