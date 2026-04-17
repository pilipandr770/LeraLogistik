# Стан проекту

_Актуально на момент передачі архіву._

## Що повністю готово ✅

### Інфраструктура
- `pyproject.toml` зі всіма залежностями
- `.env.example` з повним набором змінних
- `.gitignore` (захист від коміту секретів)
- `docker-compose.yml` для локального Postgres
- `render.yaml` для деплою на Render.com з правильною обробкою `DATABASE_URL`

### Конфігурація
- `app/config.py` — Pydantic Settings з автоматичним нормалізатором `postgres://` → `postgresql+asyncpg://`
- Feature-прапори для безпечного увімкнення/вимкнення агентів

### База даних
- `app/db/session.py` — async engine + session factory
- `app/db/models.py` — повна схема ORM:
  - `carriers` — реєстр перевізників
  - `loads` — вантажі з біржі
  - `vehicles` — вільний транспорт
  - `matches` — AI-пропозиції
  - `negotiations` — переговори
  - `negotiation_messages` — повідомлення
  - `deals` — укладені угоди
- Alembic налаштовано (`alembic.ini` + `migrations/env.py` + `script.py.mako`)

### Адаптери бірж
- `app/adapters/base.py` — абстрактний інтерфейс `ExchangeAdapter` + нормалізовані dataclass'и (`NormalizedLoad`, `NormalizedVehicle`, `SearchFilter`)
- `app/adapters/lardi.py` — робочий адаптер Lardi-Trans API v2:
  - Autentifikatsiya через `Authorization` header
  - `search_loads` (POST `/proposals/search/cargo`)
  - `search_vehicles` (POST `/proposals/search/lorry`)
  - `health_check` через `/references/countries`
  - Retry з exponential backoff через tenacity
  - Нормалізація raw-response у наші dataclass'и

### Сервіси
- `app/services/ingestion.py` — `IngestionService` з upsert-логікою за `(source, external_id)`. Автоматично створює `Carrier` при першій зустрічі.

### AI-агенти
- `app/agents/matcher.py` — `MatcherAgent`:
  - Префільтр кандидатів у чистому Python (відстань через haversine, body-type, capacity)
  - LLM-скоринг через Claude Haiku з JSON-відповіддю
  - Записує результат у таблицю `matches`
  - **Безпечний**: не робить зовнішніх дій

### Веб-інтерфейс
- `app/main.py` — FastAPI app з lifespan, monting static
- `app/routes/health.py` — `/health` для Render
- `app/routes/dashboard.py` — головна сторінка + HTMX-партіал лічильників
- `app/routes/loads_routes.py` — операційні ендпоінти:
  - `POST /ops/lardi/health`
  - `POST /ops/lardi/ingest/loads`
  - `POST /ops/lardi/ingest/vehicles`
  - `POST /ops/matcher/run`
  - `GET /ops/loads/{id}`
  - `POST /ops/matches/{id}/reject`
- `app/templates/base.html` — базовий шаблон з Tailwind + HTMX + Alpine.js через CDN
- `app/templates/dashboard.html` — дашборд з лічильниками (live через HTMX), кнопками операцій, топ-матчами, списком останніх вантажів/транспорту
- `app/templates/components/counters.html` — компонент лічильників
- `app/templates/load_detail.html` — сторінка деталей вантажу з матчами

### Тести
- `tests/test_lardi_adapter.py` — 4 тести нормалізації Lardi
- `tests/test_matcher.py` — 3 тести префільтра Matcher
- **Усі 7 тестів проходять** ✅ (перевірено в середовищі збірки)

### Імпорт-смоук
- Перевірено, що `from app.main import app` працює без помилок
- 14 роутів зареєстровані

---

## Що потребує вашої дії перед першим запуском ⚠️

1. **Заповнити `.env`** реальними значеннями:
   - `LARDI_API_TOKEN` — з https://lardi-trans.com/log/settings/api/ (має бути активованим через підтримку Lardi)
   - `ANTHROPIC_API_KEY` — з https://console.anthropic.com
   - `APP_SECRET_KEY` — згенерувати через `python -c "import secrets; print(secrets.token_urlsafe(48))"`

2. **Згенерувати першу Alembic-міграцію**:
   ```bash
   docker compose up -d
   alembic revision --autogenerate -m "initial schema"
   alembic upgrade head
   ```

3. **Перевірити мапінг полів Lardi.** Коли отримаєте перший реальний response від Lardi API, у таблиці `loads`/`vehicles` колонка `raw_payload` збереже його повністю. Порівняйте з тим, що вийшло в інших колонках (`from_city`, `weight_tons` тощо). Якщо щось не збіглося — правте `_to_normalized_load` та `_to_normalized_vehicle` у `app/adapters/lardi.py`. Там є коментарі `# TODO` — зокрема для парсингу дат, структура яких у Lardi складна.

---

## Що НЕ реалізовано (свідомо, для наступних ітерацій) ❌

### Фонові завдання
- **APScheduler** для автоматичного періодичного ingestion (зараз усе запускається вручну з дашборду — це правильно для MVP)

### Інші AI-агенти
- **Pricing agent** — пропонувати ринкову ціну на основі маршруту + історії угод
- **Negotiator agent** — вести переписку з перевізниками, готувати чернетки
- **Risk agent** — перевіряти нових перевізників (VIES, ЄДРПОУ, історія на Lardi)
- **Document agent** — генерувати CMR, договори, акти

### Канали комунікації
- Telegram Bot API
- Viber Business API
- Email parsing (IMAP → AI parser → loads)

### Дашборд
- Карта з точками машин і грузів (Leaflet)
- Сторінка деталей транспорту (симетрична до `load_detail.html`)
- Сторінка переговорів (kanban)
- Сторінка угод з фінансовими показниками
- Фільтри / пошук

### Інтеграції
- OpenRouteService для справжньої відстані (зараз — haversine)
- Monobank API для виставлення рахунків
- ЕДРПОУ для перевірки контрагентів

### Додаткові тести
- Тести роутів (pytest + httpx TestClient)
- Тести ingestion-сервісу з тимчасовою SQLite in-memory
- E2E-тест full-loop (ingest → match → approve)

### Друга біржа
- Коли MVP запрацює — додати адаптер `app/adapters/della.py` як другий `ExchangeAdapter`. Решта коду не зміниться.

---

## Відомі обмеження і ризики

1. **API-токен Lardi на скріншоті — "НЕ АКТИВНИЙ".** Потрібен запит до підтримки Lardi про активацію і тариф. Приблизно €30-100/міс.

2. **Структури Lardi можуть відрізнятися.** Документація Lardi показує схему полів, але в реальних response'ах бувають варіації. Перший запуск з реальним токеном покаже, що підправити. Всі сирі payload-и зберігаються в `raw_payload`, тож це не критично.

3. **Tailwind через CDN** зручний для MVP, але перед production варто зібрати локально або перейти на Tailwind CLI у build-кроці.

4. **Немає автентифікації у дашборді.** MVP, одна людина, прихований URL. Перед production — додати basic auth або (краще) HTTP-based SSO.

5. **Rate limits Lardi не задокументовані.** `LARDI_POLL_INTERVAL_SECONDS=60` — консервативно. Якщо Lardi поверне 429, збільшити.

6. **Claude Haiku для matcher** — вибраний задля дешевизни. Sonnet 4.6 дає якісніший reasoning, але в 5-10 разів дорожчий. Для MVP Haiku достатньо; якість можна підняти пізніше перемикачем `ANTHROPIC_MODEL_FAST`.
