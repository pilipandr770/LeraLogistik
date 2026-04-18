"""AI Chatbot endpoint — powers the mini-site chat widget.

Each company can enable a chatbot on their public profile page (/p/{slug}).
The chatbot answers questions from potential clients using Claude, guided by:
  1. A system prompt the company configures (their services, routes, prices, etc.)
  2. Basic company facts pulled from the DB (name, country, role, verified status)

Security:
  - No auth required (public endpoint — it's a widget for clients)
  - Rate limiting is handled at the infra level (Nginx / Render)
  - User input is passed to Claude as a user message, never injected into the
    system prompt — prevents prompt injection attacks
  - Chat history is stored only in the browser (sessionStorage), not in our DB.
    This means no PII is stored server-side from the widget conversation.
"""

from __future__ import annotations

import logging

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Company
from app.db.session import get_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chatbot"])
templates = Jinja2Templates(directory="app/templates")

_MAX_USER_MSG_LEN = 1000
_MAX_HISTORY_TURNS = 10  # max turns we accept back from browser to limit token costs


@router.post("/p/{slug}", response_class=HTMLResponse)
async def chat_message(
    slug: str,
    request: Request,
    message: str = Form(...),
    # History sent by the browser as JSON string: [{"role":"user","content":"..."},...]
    history: str = Form(default="[]"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Process one chat turn and return an HTML partial for HTMX."""
    settings = get_settings()

    if not settings.anthropic_api_key:
        return templates.TemplateResponse(
     request,
     "chatbot/_bubble.html",
     {
                "role": "assistant",
                "content": "Вибачте, чат тимчасово недоступний.",
     },
 )

    # Load company
    result = await session.execute(select(Company).where(Company.slug == slug))
    company = result.scalar_one_or_none()
    if not company or not company.chatbot_enabled:
        raise HTTPException(status_code=404)

    # Sanitise user message
    user_text = message.strip()[:_MAX_USER_MSG_LEN]
    if not user_text:
        raise HTTPException(status_code=422, detail="Empty message")

    # Build system prompt from company config + auto-facts
    role_label = {
        "carrier": "транспортна компанія (перевізник)",
        "shipper": "компанія-відправник вантажів",
        "forwarder": "експедиторська компанія",
    }
    company_role = role_label.get(company.role, "логістична компанія")

    verification_note = (
        "Компанія верифікована через державні реєстри на платформі TruckLink."
        if company.is_verified
        else ""
    )

    company_facts = (
        f"Назва компанії: {company.name}. "
        f"Тип: {company_role}. "
        f"Країна: {company.country}. "
        f"{verification_note} "
        f"{'Слоган: ' + company.tagline + '.' if company.tagline else ''}"
    )

    if company.chatbot_system_prompt:
        system = f"{company.chatbot_system_prompt.strip()}\n\n{company_facts}"
    else:
        system = (
            f"Ти — чемний, чіткий AI-асистент компанії «{company.name}». "
            f"Відповідай ТІЛЬКИ на питання, пов'язані з логістикою та послугами цієї компанії. "
            f"Якщо питання не стосується перевезень — ввічливо поверни розмову до теми. "
            f"Відповідай українською мовою. Будь лаконічним — максимум 3–4 речення. "
            f"{company_facts}"
        )

    # Parse browser history (last N turns only)
    import json as _json

    messages: list[dict] = []
    try:
        raw_history = _json.loads(history)
        if isinstance(raw_history, list):
            for turn in raw_history[-_MAX_HISTORY_TURNS:]:
                role = turn.get("role", "")
                content = str(turn.get("content", ""))[:_MAX_USER_MSG_LEN]
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})
    except Exception:
        pass  # malformed history — ignore, start fresh

    messages.append({"role": "user", "content": user_text})

    # Call Claude
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=settings.anthropic_model_fast,
            max_tokens=512,
            system=system,
            messages=messages,
        )
        reply = response.content[0].text if response.content else "Вибачте, не вдалося отримати відповідь."
    except Exception:
        log.exception("Chatbot Claude call failed for company %s", company.id)
        reply = "Вибачте, сталася помилка. Спробуйте пізніше."

    return templates.TemplateResponse(
     request,
     "chatbot/_bubble.html",
     {
            "role": "assistant",
            "content": reply,
            "user_message": user_text,
     },
 )
