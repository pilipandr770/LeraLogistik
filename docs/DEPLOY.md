# Деплой на Render.com

## Передумови

- Репозиторій на GitHub (приватний)
- Акаунт на Render.com
- Уже існуюча Postgres БД на Render (у тебе є)
- Активований `LARDI_API_TOKEN`
- `ANTHROPIC_API_KEY`

## Крок 1 — запуш у GitHub

```bash
git init
git add .
git commit -m "Initial commit: Lera Logistics MVP"

# Створи приватний репо на GitHub (через UI або gh cli)
gh repo create lera-logistics --private --source=. --push
```

**Перевір, що `.env` НЕ в коміті!**

```bash
git ls-files | grep .env
# має показати тільки .env.example, не .env
```

## Крок 2 — створи Web Service у Render

1. Render Dashboard → **New** → **Web Service**
2. Connect GitHub → обери репо `lera-logistics`
3. Render автоматично побачить `render.yaml` і запропонує Blueprint-деплой. Погоджуйся.

Якщо не підхопило автоматично, створи вручну:

| Поле | Значення |
| --- | --- |
| Name | `lera-logistics` |
| Region | `Frankfurt` |
| Branch | `main` |
| Runtime | `Python 3` |
| Build Command | `pip install -e . && alembic upgrade head` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Plan | `Starter` ($7/міс) |

## Крок 3 — приєднай Postgres

В налаштуваннях сервісу → **Environment** → додай змінну:

- Key: `DATABASE_URL`
- Value: натисни "Add from Database" і обери свою існуючу Postgres. Render автоматично підставить правильний connection string.

**Увага:** Render дає `postgres://user:pass@host/db`. Наш код у `app/config.py` автоматично перетворює це на `postgresql+asyncpg://...`, тож нічого додатково робити не треба.

## Крок 4 — секрети

В тій же вкладці **Environment** додай:

| Key | Value | Тип |
| --- | --- | --- |
| `APP_SECRET_KEY` | згенеруй: `python -c "import secrets; print(secrets.token_urlsafe(48))"` | Secret |
| `LARDI_API_TOKEN` | твій Lardi токен | Secret |
| `ANTHROPIC_API_KEY` | sk-ant-... | Secret |
| `APP_ENV` | `production` | Plain |
| `APP_DEBUG` | `false` | Plain |

Необов'язкові (залиш дефолти з `.env.example`, якщо не потрібно змінювати):

- `LARDI_API_LANGUAGE=uk`
- `LARDI_POLL_INTERVAL_SECONDS=60`
- `ANTHROPIC_MODEL=claude-sonnet-4-6`
- `ANTHROPIC_MODEL_FAST=claude-haiku-4-5-20251001`

## Крок 5 — деплой

Після збереження змінних Render сам запустить білд. Дивись у логи → має пройти:

```
Installing dependencies...
Running migrations (alembic upgrade head)...
Starting service: uvicorn app.main:app ...
==> Your service is live 🎉
```

## Крок 6 — перевірка

- `https://lera-logistics.onrender.com/health` → `{"status":"ok"}`
- `https://lera-logistics.onrender.com/` → дашборд
- `https://lera-logistics.onrender.com/docs` → Swagger

Натисни "Перевірити Lardi API" — якщо зелений, усе працює end-to-end.

## Типові проблеми

### Build failed: `alembic: command not found`

У `render.yaml` перед `alembic upgrade head` має бути `pip install -e .`. Alembic встановлюється як dependency.

### `DATABASE_URL` не підхопилося

Перевір, що в Environment саме `DATABASE_URL`, а не `DB_URL` або ще щось. І що база приєднана через "Add from Database", а не вставлена вручну (так Render при ротації паролів оновить значення автоматично).

### Starter plan спить

Render Starter має cold starts після 15 хв неактивності. Для логістики це не критично (оператор і так заходить один раз на сесію роботи), але health check з Render'а буде час від часу "будити" сервіс.

Якщо хочеш, щоб сервіс завжди жив, апгрейди на Standard.

### Занадто довгий Claude API call → timeout

Render має 30-секундний timeout на HTTP-запити. Якщо Matcher обробляє багато вантажів, це може не вкластися. Рішення на майбутнє — винести Matcher у background worker (ще один сервіс на Render типу Background Worker).

## Моніторинг логів

```bash
# Якщо встановлений render CLI
render logs -s lera-logistics --tail
```

Або через веб-інтерфейс: Dashboard → Service → Logs.

Рекомендую одразу підключити **Render's Logtail integration** або налаштувати **structlog** (уже в залежностях) для JSON-логів — це сильно допомагає шукати помилки в продакшені.

## Вартість

| Компонент | План | Ціна |
| --- | --- | --- |
| Web Service | Starter | $7/міс |
| Postgres | у тебе вже є | власний |
| Anthropic API | pay-per-use | ~$5-30/міс для MVP |
| Lardi API | ? | залежить від домовленостей з Lardi |

**Разом для MVP: ~$15-50/міс.**
