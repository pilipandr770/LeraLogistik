# Нотатки по Lardi-Trans API

## Ключові посилання

- Docs index: https://api.lardi-trans.com/v2/docs/index.html
- Налаштування токену: https://lardi-trans.com/log/settings/api/
- Окремо російською: https://api.lardi-trans.com/v2/docs/ru/
- Українською: https://api.lardi-trans.com/v2/docs/uk/
- Англійською: https://api.lardi-trans.com/v2/docs/en/

## Автентифікація

Токен передається в заголовку `Authorization` **без** префікса `Bearer`:

```
Authorization: 21OAM7PT2NN000005616
```

Параметр `language` (uk/ru/en) передається в query string: `?language=uk`.

Ліміти (з UI):
- Максимум активних токенів одночасно: 1
- Максимум токенів усього: 10
- Час, протягом якого не можна видалити токен після створення: 10 хв

## Основні ендпоінти, які ми використовуємо

| Метод | URL | Призначення |
| --- | --- | --- |
| POST | `/v2/proposals/search/cargo` | пошук вантажів |
| POST | `/v2/proposals/search/lorry` | пошук вільного транспорту |
| GET | `/v2/proposals/view/cargo/{id}` | деталь одного вантажу |
| GET | `/v2/proposals/view/lorry/{id}` | деталь одного транспорту |
| GET | `/v2/references/countries` | довідник країн (=health check) |
| GET | `/v2/users/{id}` | дані про перевізника/клієнта |

## Формат пошукового body

```json
{
  "filter": {
    "directionFrom": [{"countrySign": "UA"}],
    "directionTo": [{"countrySign": "PL"}],
    "dateFrom": "2026-04-17"
  },
  "options": {
    "page": 0,
    "perPage": 50
  }
}
```

## Формат відповіді (пошук вантажів)

```json
{
  "content": [
    {
      "id": 12345,
      "cargoName": "...",
      "waypointListSource": [...],
      "waypointListTarget": [...],
      "size": {"weight": 20.0, "volume": 86.0},
      "bodyTypes": [...],
      "payment": {...},
      "contact": {...}
    }
  ],
  "paginator": {
    "current": 0,
    "perPage": 150,
    "pages": 0
  }
}
```

## Важливо: що перевірити з першим реальним токеном

Документація Lardi не завжди ідеально відображає реальність. **Обов'язково** зроби перший пробний виклик і збережи сирий JSON:

```bash
curl -X POST "https://api.lardi-trans.com/v2/proposals/search/cargo?language=uk" \
  -H "Authorization: <твій_токен>" \
  -H "Content-Type: application/json" \
  -d '{
    "filter": {"directionFrom": [{"countrySign": "UA"}]},
    "options": {"page": 0, "perPage": 5}
  }' | python -m json.tool > lardi_sample.json
```

Потім порівняй `lardi_sample.json` з тим, що очікує наш адаптер у `app/adapters/lardi.py` → методи `_to_normalized_load` і `_to_normalized_vehicle`.

**Поля, які ймовірно треба буде уточнити:**

1. **Точки маршруту** — доки в документації є варіації `waypointListSource` vs `directionFrom`. Адаптер обробляє обидва випадки через fallback.

2. **Дати** — формат Lardi невідомий з документації. У поточній версії `pickup_date_from` / `pickup_date_to` ставляться в `None` з TODO-коментарем. Коли побачиш реальний формат, додай парсинг у `_to_normalized_load`.

3. **Контакти** — структура `contact.phone` може бути або рядком, або об'єктом `{"number": "..."}`. Адаптер перевіряє обидва варіанти.

4. **Валюта** — може прийти як об'єкт `{"sign": "UAH", "id": 1}` або просто рядок. Перевір на реальних даних.

5. **`bodyTypes`** — масив об'єктів з `name` або з `id` (треба резолвити через довідник)? Адаптер бере `name`, якщо його немає — може зламатися.

## Стратегія оновлення адаптера

Адаптер спроектовано так, щоб одна зміна в одному місці (`_to_normalized_*`) не зачіпала решту коду. Якщо бачиш, що поле не заповнилось:

1. Знайди в `raw_payload` (колонка JSONB таблиці `loads`) як Lardi його реально назвав.
2. Відредагуй відповідний метод у `app/adapters/lardi.py`.
3. Запусти `pytest tests/test_lardi_adapter.py`.
4. Якщо тести зелені, перезапусти додаток — нові ingest'и підуть з правильним мапінгом.

## Rate limits

У публічній документації Lardi не розкриває ліміти. Практика показує:
- Поодинокі запити працюють швидко (< 500 мс)
- Масовий polling кожні 10 секунд може призвести до throttling
- Рекомендую `LARDI_POLL_INTERVAL_SECONDS=60` на старті

Якщо Lardi поверне `429 Too Many Requests`, наш адаптер через tenacity зробить exponential backoff (4 секунди, 8, 16). Після трьох невдач — виняток.

## Перехід до real-time (далеке майбутнє)

Lardi має webhooks для деяких подій (зміна заявки, новий тендер) — треба писати підтримці, це платна функція. Для MVP polling достатньо.

## Окремо про тендери

Ми поки не використовуємо ендпоінти тендерів (`/v2/tenders/...`), але вони є в адаптері майбутнім. Тендери — це коли Lera хоче опублікувати свій груз на біржу і приймати ставки від перевізників. Це потрібно буде для стадії "прямі клієнти" після MVP.
