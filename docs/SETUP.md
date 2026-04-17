# Налаштування локальної розробки

## 1. Передумови

### Python 3.12+

Перевір:

```bash
python --version
# Python 3.12.x або вище
```

На Windows краще поставити Python з [python.org](https://www.python.org/downloads/) і обов'язково поставити галочку "Add Python to PATH".

### Docker Desktop

Для локального Postgres. [Завантажити](https://www.docker.com/products/docker-desktop/).

Перевір:

```bash
docker --version
docker compose version
```

### Git

```bash
git --version
```

### VS Code + Claude Code

- [VS Code](https://code.visualstudio.com/)
- Розширення: Python, Pylance, Ruff
- [Claude Code](https://docs.claude.com/en/docs/claude-code) — CLI-агент Anthropic, встановлюється окремо і запускається в терміналі

## 2. Клонування і структура

```bash
git clone <your-github-url> lera-logistics
cd lera-logistics
code .
```

Відкрий у VS Code.

## 3. Віртуальне середовище

### Варіант А: uv (рекомендовано — швидко)

Встанови `uv`:

```bash
# Windows PowerShell
irm https://astral.sh/uv/install.ps1 | iex

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Створи venv і постав залежності:

```bash
uv venv
uv pip install -e ".[dev]"
```

Активація venv:

```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### Варіант Б: звичайний pip

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install --upgrade pip
pip install -e ".[dev]"
```

## 4. Postgres через Docker

```bash
docker compose up -d
```

Перевір:

```bash
docker compose ps
# має бути lera_postgres   running   0.0.0.0:5432->5432/tcp
```

## 5. Змінні оточення

```bash
cp .env.example .env
```

Відкрий `.env` і заповни:

```
APP_SECRET_KEY=<згенеруй командою нижче>
LARDI_API_TOKEN=21OAM7PT2NN000005616    # твій токен з Lardi
ANTHROPIC_API_KEY=sk-ant-api03-...       # з https://console.anthropic.com
```

Генерація секретного ключа:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Важливо:** `.env` у `.gitignore`. Якщо раптом побачив `.env` у списку `git status` — щось не так.

## 6. Міграції

Перша міграція (генерує схему з ORM-моделей):

```bash
alembic revision --autogenerate -m "initial schema"
```

З'явиться файл у `migrations/versions/`. Відкрий його, переконайся що всі таблиці там є (loads, vehicles, carriers, matches, negotiations, negotiation_messages, deals).

Застосуй:

```bash
alembic upgrade head
```

Перевір у Postgres:

```bash
docker exec -it lera_postgres psql -U lera -d lera -c "\dt"
# має показати список таблиць
```

## 7. Запуск додатка

```bash
uvicorn app.main:app --reload
```

Відкрий http://localhost:8000 — має з'явитися дашборд.

Також доступні:

- http://localhost:8000/docs — Swagger UI (автоматичний FastAPI)
- http://localhost:8000/health — health check

## 8. Перший реальний тест Lardi API

На дашборді натисни **"Перевірити Lardi API"**. Якщо токен активований і правильний, побачиш `✅ Lardi доступний`. Якщо ні — перевір:

1. Токен активований на стороні Lardi? (це робить їх підтримка)
2. Чи підписаний тариф API?
3. Перевір мережу — `curl https://api.lardi-trans.com/v2/references/countries -H "Authorization: <твій_токен>"` має повернути JSON.

Якщо все ок — натисни **"Забрати вантажі з Lardi (UA)"**. Через кілька секунд у таблиці `loads` мають з'явитися рядки. Подивися через psql або перезавантаж дашборд.

## 9. Перший AI-матчер

Після того як у БД є і вантажі і транспорт, натисни **"Запустити AI Matcher"**. Через 10-30 секунд у таблиці `matches` з'являться AI-оцінки. Подивись на дашборді у блоці "Найкращі AI-матчі".

Важливо: кожен виклик Matcher'а коштує ~$0.01-0.05 залежно від кількості вантажів/машин (Claude Haiku дуже дешевий).

## 10. Тести

```bash
pytest
# має бути 7 passed
```

З покриттям:

```bash
pytest --cov=app
```

Лінтер:

```bash
ruff check app/
ruff format app/
```

## Типові проблеми

### `ModuleNotFoundError: No module named 'asyncpg'`

Забув активувати venv або `pip install -e ".[dev]"` не виконувався.

### `connection refused to 127.0.0.1:5432`

Docker не запущений або контейнер Postgres впав. `docker compose up -d`.

### Alembic не знаходить моделі

Перевір, що `alembic.ini` і `migrations/env.py` на місці. Якщо `alembic revision --autogenerate` дає пусту міграцію — означає, що `target_metadata` порожній, перевір, що `from app.db.models import Base` у `migrations/env.py` не викидає помилку.

### `LARDI_API_TOKEN is not configured`

`.env` не створений або не завантажується. Переконайся, що запускаєш uvicorn з кореневої папки проекту (де лежить `.env`).

### `429 Too Many Requests` від Lardi

Збільши `LARDI_POLL_INTERVAL_SECONDS` у `.env`. Або зменши `per_page` у `SearchFilter`.
