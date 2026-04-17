# Архітектура Lera Logistics

## Layered view

```
┌───────────────────────────────────────────────────────────────┐
│ Presentation layer                                            │
│ ─ FastAPI routes (app/routes/)                                │
│ ─ Jinja2 templates + HTMX + Alpine.js + Tailwind              │
└──────────────────────────┬────────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────────┐
│ Services layer (app/services/)                                │
│ ─ IngestionService: adapter → ORM                             │
│ ─ (future) PricingService, NegotiationService, DealService    │
└──────────────────────────┬────────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────────┐
│ Agents layer (app/agents/)                                    │
│ ─ MatcherAgent                                                │
│ ─ (future) PricingAgent, NegotiatorAgent, RiskAgent           │
└──────────────────────────┬────────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────────┐
│ Persistence layer (app/db/)                                   │
│ ─ SQLAlchemy 2.0 async ORM + Alembic migrations               │
└──────────────────────────┬────────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────────┐
│ External integrations                                         │
│ ─ Adapters (app/adapters/): Lardi → (future) Della, Timocom   │
│ ─ Anthropic API (Claude)                                      │
└───────────────────────────────────────────────────────────────┘
```

## Основні принципи

### 1. Adapter pattern для зовнішніх бірж

Будь-яка біржа (Lardi, Della, Timocom, Trans.eu) реалізує інтерфейс
`ExchangeAdapter`. Решта додатка ніколи не імпортує конкретну біржу
безпосередньо.

```python
# ✅ правильно
from app.adapters.lardi import LardiAdapter
async with LardiAdapter() as adapter:
    await service.ingest_loads(...)

# ❌ неправильно — агент НЕ має знати про Lardi
from app.adapters.lardi import LardiAdapter  # in MatcherAgent?  NO
```

Додавання Della:

```python
# app/adapters/della.py
class DellaAdapter(ExchangeAdapter):
    source = "della"
    async def search_loads(self, flt): ...
    async def search_vehicles(self, flt): ...
    async def health_check(self): ...
```

І все. Жодного іншого файла змінювати не треба.

### 2. Нормалізація на межі

Коли дані приходять ззовні, вони одразу перетворюються на наші
нормалізовані dataclass'и (`NormalizedLoad`, `NormalizedVehicle`).
Далі всередині системи жодних `dict`'ів із зовнішніх API не ходить.

Сирий payload зберігається в колонці `raw_payload` (JSONB) кожної таблиці — це страховка на випадок, коли зовнішнє API змінить схему.

### 3. Прогресивна автоматизація через feature-прапори

У `.env`:

```
AGENT_MATCHER_ENABLED=true         # безпечно: лише пише в БД
AGENT_PRICING_ENABLED=true         # безпечно: лише пропонує ціну
AGENT_NEGOTIATOR_AUTO_SEND=false   # НЕБЕЗПЕЧНО: агент сам відправляє повідомлення
AGENT_AUTO_ACCEPT_DEALS=false      # НЕБЕЗПЕЧНО: агент сам укладає угоди
```

Два нижніх прапори — **не вмикати**, поки не буде підтвердженої людиною історії у 100+ угод на цих самих перевізниках. Кожне повідомлення, яке AI надсилає самостійно, це юридична дія вашої компанії.

### 4. Спеціалізовані агенти замість одного великого

**Погано:** один "розумний асистент", який робить усе.

**Добре:** кожен агент має вузьку задачу, власний промпт, власний набір інструментів, власні метрики якості.

| Агент | Статус | Що робить | Безпека |
| --- | --- | --- | --- |
| Matcher | ✅ реалізовано | для кожного вантажу знаходить топ-N машин | безпечний (тільки DB-запис) |
| Pricing | 🔜 TODO | пропонує ціну для нової угоди | безпечний |
| Negotiator | 🔜 TODO | готує чернетки повідомлень | небезпечний при `auto_send=true` |
| Risk | 🔜 TODO | перевіряє нового перевізника (ЄДРПОУ, історія) | безпечний |
| Parser | 🔜 TODO | розбирає email/повідомлення на структуру | безпечний |
| Document | 🔜 TODO | генерує CMR, договір, рахунок | безпечний |

### 5. Ідемпотентність ingestion

Unique constraint `(source, external_id)` на `loads` і `vehicles` гарантує, що повторне завантаження тих самих пропозицій не створить дублікатів. `IngestionService` повертає "нових рядків: N", щоб було видно реальну новизну.

### 6. async по всьому стеку

FastAPI + SQLAlchemy 2 async + httpx async + anthropic async. Це важливо, коли Matcher буде викликати Claude для 10 кандидатів по 10 вантажах — послідовні виклики це 100 HTTP round-trip'ів, паралельні через `asyncio.gather` це ~10 секунд.

## Потік даних: від заявки до матчу

```
1. Оператор натискає "Забрати вантажі з Lardi"
      │
      ▼
2. POST /ops/lardi/ingest/loads
      │
      ▼
3. LardiAdapter.search_loads(filter)
      │
      │  POST https://api.lardi-trans.com/v2/proposals/search/cargo
      │  Authorization: <token>
      │
      ▼
4. Raw JSON → список NormalizedLoad
      │
      ▼
5. IngestionService.ingest_loads()
      │  для кожного: upsert за (source, external_id)
      │
      ▼
6. Рядки в таблиці `loads` зі статусом NEW
      │
      ▼
7. Оператор натискає "Запустити AI Matcher"
      │
      ▼
8. POST /ops/matcher/run
      │
      ▼
9. MatcherAgent для кожного NEW-вантажу:
      │  а) префільтр: країна, body_type, capacity, haversine
      │  б) топ-10 кандидатів → Claude Haiku (один API-виклик)
      │  в) парс JSON-відповіді
      │  г) запис у таблицю `matches` + переведення Load.status → MATCHED
      │
      ▼
10. Оператор бачить топ-матчі на дашборді,
    переходить на сторінку вантажу,
    відхиляє слабкі або промотує сильні в переговори.
```
