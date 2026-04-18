"""
Seed script — creates demo users, companies, loads, vehicles, matches and deals
for investor/client demos. Run once; safe to re-run (skips existing emails).

Usage:
    python seed_demo.py

Remove all demo data:
    python seed_demo.py --drop
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.models import (
    Base,
    Carrier,
    Company,
    Deal,
    DealStatus,
    Load,
    LoadStatus,
    Match,
    MatchStatus,
    User,
    UserRole,
    Vehicle,
    VehicleStatus,
)
from app.config import get_settings

_settings = get_settings()

import bcrypt as _bcrypt_lib

def _hash(plain: str) -> str:
    return _bcrypt_lib.hashpw(plain.encode(), _bcrypt_lib.gensalt(rounds=4)).decode()

DEMO_PASSWORD = "Demo1234!"

# ── Demo data ─────────────────────────────────────────────────────────────────
SHIPPERS = [
    {"name": "АгроЕкспорт ТОВ",        "country": "UA", "slug": "agroeksport",     "email": "shipper1@demo.trucklink.app"},
    {"name": "EuroCargo GmbH",           "country": "DE", "slug": "eurocargo",       "email": "shipper2@demo.trucklink.app"},
    {"name": "ПромТранс-Захід",          "country": "UA", "slug": "promtrans",       "email": "shipper3@demo.trucklink.app"},
    {"name": "Baltic Freight Solutions", "country": "PL", "slug": "balticfreight",   "email": "shipper4@demo.trucklink.app"},
]

CARRIERS = [
    {
        "name": "Перевозчик Плюс",
        "country": "UA",
        "slug": "pereveznyk-plus",
        "email": "carrier1@demo.trucklink.app",
        "phone": "+380631234567",
        "website": "https://pereveznyk.demo.ua",
        "tagline": "Надійні перевезення по Україні та ЄС",
        "description": (
            "Перевозчик Плюс — українська транспортна компанія з 10-річним досвідом "
            "міжнародних перевезень. Парк із 25 вантажівок, спеціалізація: тент, реф, "
            "негабаритні вантажі. Регулярні рейси Україна–Польща–Німеччина. "
            "Всі водії з дозволами ADR, страхування CMR."
        ),
        "plates": ["AA 1234 AB", "AA 5678 CD", "KA 9012 EF"],
    },
    {
        "name": "Trans-EU Sp. z o.o",
        "country": "PL",
        "slug": "transeu",
        "email": "carrier2@demo.trucklink.app",
        "phone": "+48501234567",
        "website": "https://transeu.demo.pl",
        "tagline": "Ваш партнер у логістиці Центральної Європи",
        "description": (
            "Trans-EU — польський перевізник з флотом 40 тягачів і рефрижераторів. "
            "Покриваємо маршрути PL–DE–FR–NL–CZ–SK. Температурний контроль від -25°C. "
            "Сертифікати ISO 9001, HACCP для харчових вантажів."
        ),
        "plates": ["WA 1111 B", "KR 2222 C", "WA 3333 D"],
    },
    {
        "name": "Степ Авто",
        "country": "UA",
        "slug": "step-avto",
        "email": "carrier3@demo.trucklink.app",
        "phone": "+380672345678",
        "website": "https://stepavto.demo.ua",
        "tagline": "Перевезення зернових та агропродукції",
        "description": (
            "Степ Авто спеціалізується на перевезенні агропродукції: зернових, олій, "
            "добрив. Парк: 15 зерновозів, 10 тентів, 5 цистерн. "
            "Власні ваги та фітосанітарний контроль. Регіони: Дніпро, Запоріжжя, Херсон → EU."
        ),
        "plates": ["AE 4444 GH", "AE 5555 IJ", "ZP 6666 KL"],
    },
    {
        "name": "LogiTruck DE",
        "country": "DE",
        "slug": "logitruck-de",
        "email": "carrier4@demo.trucklink.app",
        "phone": "+4930987654",
        "website": "https://logitruck.demo.de",
        "tagline": "Next-day logistics across Western Europe",
        "description": (
            "LogiTruck DE is a German carrier specializing in just-in-time deliveries "
            "for automotive and manufacturing industries. Fleet of 60 mega-trucks, "
            "real-time GPS tracking. Routes: DE–FR–IT–ES–PL. Partner of DB Schenker network."
        ),
        "plates": ["B AB 1234", "M CD 5678", "HH EF 9012"],
    },
]

FORWARDERS = [
    {"name": "ТопЛогістик",        "country": "UA", "slug": "toplogistik",       "email": "forwarder1@demo.trucklink.app"},
    {"name": "EuroForward B.V.",   "country": "NL", "slug": "euroforward",       "email": "forwarder2@demo.trucklink.app"},
]

ROUTES = [
    ("UA", "Київ",   "PL", "Варшава",  ["tent"],             18.5, 46),
    ("UA", "Одеса",  "DE", "Берлін",   ["refrigerator"],     22.0, 55),
    ("PL", "Краків", "DE", "Мюнхен",   ["tent", "tilt"],     15.0, 38),
    ("UA", "Харків", "UA", "Львів",    ["tent"],             12.0, 28),
    ("DE", "Берлін", "FR", "Париж",    ["tent"],             20.0, 50),
    ("UA", "Дніпро", "HU", "Будапешт", ["refrigerator"],     14.0, 35),
    ("PL", "Гданськ","SE", "Стокгольм",["open_top"],          8.0, 20),
    ("UA", "Запоріжжя","PL","Вроцлав", ["tent"],             16.0, 40),
]

CARGO_NAMES = [
    "Соняшникова олія",
    "Автозапчастини",
    "Обладнання для фермерства",
    "Продукти харчування (заморожені)",
    "Будівельні матеріали",
    "Меблі",
    "Хімікати (непебезпечні)",
    "Зернові (пшениця)",
]

BODY_TYPES = ["tent", "refrigerator", "tilt", "open_top", "container"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days(n: int) -> timedelta:
    return timedelta(days=n)


async def seed(session: AsyncSession) -> None:
    print("🌱  Seeding demo data...")

    # ── 1. Admin user ──────────────────────────────────────────────────────────
    admin_email = "admin@demo.trucklink.app"
    if not (await session.scalar(select(User).where(User.email == admin_email))):
        admin_company = Company(name="TruckLink Platform", country="UA", slug="trucklink-admin",
                                trust_score=100, is_verified=True)
        session.add(admin_company)
        await session.flush()
        session.add(User(
            email=admin_email,
            password_hash=_hash(DEMO_PASSWORD),
            role=UserRole.ADMIN,
            company_id=admin_company.id,
            is_active=True,
            is_email_verified=True,
        ))
        print(f"  ✅ Admin: {admin_email}")

    # ── 2. Shipper companies + users ───────────────────────────────────────────
    shipper_companies: list[Company] = []
    for s in SHIPPERS:
        if not (await session.scalar(select(User).where(User.email == s["email"]))):
            c = Company(name=s["name"], country=s["country"], slug=s["slug"],
                        trust_score=random.randint(60, 95), is_verified=True,
                        tagline="Шукаємо надійних перевізників для наших вантажів",
                        phone=f"+38063{random.randint(1000000,9999999)}")
            session.add(c)
            await session.flush()
            session.add(User(
                email=s["email"],
                password_hash=_hash(DEMO_PASSWORD),
                role=UserRole.SHIPPER,
                company_id=c.id,
                is_active=True,
                is_email_verified=True,
            ))
            shipper_companies.append(c)
            print(f"  ✅ Shipper: {s['email']}")
        else:
            result = await session.execute(select(User).where(User.email == s["email"]))
            u = result.scalar_one()
            if u.company_id:
                c = await session.get(Company, u.company_id)
                shipper_companies.append(c)

    # ── 3. Carrier companies + users + vehicles ────────────────────────────────
    carrier_companies: list[Company] = []
    for idx, c_data in enumerate(CARRIERS):
        existing_user = await session.scalar(select(User).where(User.email == c_data["email"]))
        if not existing_user:
            c = Company(
                name=c_data["name"],
                country=c_data["country"],
                slug=c_data["slug"],
                trust_score=random.randint(78, 95),
                is_verified=True,
                tagline=c_data.get("tagline", "Надійні перевезення по Україні та ЄС"),
                description=c_data.get("description"),
                phone=c_data.get("phone"),
                website=c_data.get("website"),
            )
            session.add(c)
            await session.flush()

            # legacy Carrier record
            carrier_rec = Carrier(
                name=c_data["name"],
                country=c_data["country"],
                trust_score=random.randint(65, 90),
            )
            session.add(carrier_rec)
            await session.flush()

            # 2-3 vehicles per carrier
            plates = c_data.get("plates", [])
            n_vehicles = max(len(plates), random.randint(2, 3))
            for v_idx in range(n_vehicles):
                body = random.choice(BODY_TYPES)
                fr_country = c_data["country"]
                fr_city = random.choice(["Київ", "Варшава", "Берлін", "Братислава", "Відень"])
                plate = plates[v_idx] if v_idx < len(plates) else None
                session.add(Vehicle(
                    source="platform",
                    status=VehicleStatus.AVAILABLE,
                    company_id=c.id,
                    carrier_id=carrier_rec.id,
                    from_country=fr_country,
                    from_city=fr_city,
                    to_country=random.choice(["UA", "PL", "DE", "HU"]),
                    to_city=random.choice(["Київ", "Краків", "Мюнхен", "Будапешт"]),
                    body_type=body,
                    capacity_tons=random.choice([10.0, 20.0, 22.0, 25.0]),
                    capacity_m3=random.choice([50.0, 82.0, 92.0, 96.0]),
                    plate_number=plate,
                    raw_payload={"plate": plate} if plate else {},
                    available_from=_now() + _days(random.randint(0, 3)),
                    available_to=_now() + _days(random.randint(4, 14)),
                ))

            session.add(User(
                email=c_data["email"],
                password_hash=_hash(DEMO_PASSWORD),
                role=UserRole.CARRIER,
                company_id=c.id,
                is_active=True,
                is_email_verified=True,
            ))
            carrier_companies.append(c)
            print(f"  ✅ Carrier: {c_data['email']}")
        else:
            result = await session.execute(select(User).where(User.email == c_data["email"]))
            u = result.scalar_one()
            if u.company_id:
                c = await session.get(Company, u.company_id)
                # Update with richer data if missing
                if not c.description:
                    c.description = c_data.get("description")
                if not c.website:
                    c.website = c_data.get("website")
                if not c.phone and c_data.get("phone"):
                    c.phone = c_data.get("phone")
                if c_data.get("tagline"):
                    c.tagline = c_data.get("tagline")
                # Update vehicles without plate_number
                veh_result = await session.scalars(
                    select(Vehicle).where(
                        Vehicle.company_id == c.id,
                        Vehicle.plate_number.is_(None),
                    )
                )
                plates = c_data.get("plates", [])
                for i, v in enumerate(veh_result.all()):
                    if i < len(plates):
                        v.plate_number = plates[i]
                        v.raw_payload = {**(v.raw_payload or {}), "plate": plates[i]}
                carrier_companies.append(c)

    # ── 4. Forwarder companies + users ─────────────────────────────────────────
    for f_data in FORWARDERS:
        if not (await session.scalar(select(User).where(User.email == f_data["email"]))):
            c = Company(name=f_data["name"], country=f_data["country"], slug=f_data["slug"],
                        trust_score=random.randint(70, 92), is_verified=True,
                        tagline="Комплексні логістичні рішення для вашого бізнесу")
            session.add(c)
            await session.flush()
            session.add(User(
                email=f_data["email"],
                password_hash=_hash(DEMO_PASSWORD),
                role=UserRole.FORWARDER,
                company_id=c.id,
                is_active=True,
                is_email_verified=True,
            ))
            print(f"  ✅ Forwarder: {f_data['email']}")

    await session.flush()

    # ── 5. Loads posted by shippers ────────────────────────────────────────────
    loads_exist = await session.scalar(
        select(Load).where(Load.source == "platform", Load.status == LoadStatus.NEW).limit(1)
    )
    loads: list[Load] = []
    if not loads_exist:
        # Re-fetch latest user/company ids
        shipper_users = (await session.scalars(
            select(User).where(User.role == UserRole.SHIPPER)
        )).all()

        for i, (fc, fcity, tc, tcity, btypes, wt, price) in enumerate(ROUTES):
            poster = random.choice(shipper_users) if shipper_users else None
            pickup_from = _now() + _days(random.randint(1, 7))
            load = Load(
                source="platform",
                external_id=f"DEMO-{i+1:04d}",
                status=random.choice([LoadStatus.NEW, LoadStatus.NEW, LoadStatus.MATCHED]),
                from_country=fc,
                from_city=fcity,
                to_country=tc,
                to_city=tcity,
                cargo_name=CARGO_NAMES[i % len(CARGO_NAMES)],
                weight_tons=wt,
                volume_m3=round(wt * 2.5, 1),
                body_types=btypes,
                is_adr=False,
                pickup_date_from=pickup_from,
                pickup_date_to=pickup_from + _days(2),
                price_amount=price * 100,  # UAH
                price_currency="EUR",
                price_is_vat_included=True,
                posted_by_user_id=poster.id if poster else None,
            )
            session.add(load)
            loads.append(load)

        await session.flush()
        print(f"  ✅ {len(loads)} demo loads created")

    # ── 6. Vehicles (re-fetch all platform vehicles) ───────────────────────────
    vehicles = (await session.scalars(
        select(Vehicle).where(Vehicle.source == "platform")
    )).all()

    # ── 7. Matches ─────────────────────────────────────────────────────────────
    matches_exist = await session.scalar(select(Match).limit(1))
    matches: list[Match] = []
    if not matches_exist and loads and vehicles:
        pairs = list(zip(loads[:6], vehicles[:6]))
        for load, vehicle in pairs:
            m = Match(
                load_id=load.id,
                vehicle_id=vehicle.id,
                status=random.choice([MatchStatus.PROPOSED, MatchStatus.PROPOSED, MatchStatus.REVIEWED]),
                score=random.randint(62, 97),
                reasoning="AI підібрав транспорт за маршрутом та вантажопідйомністю",
                deadhead_km=random.randint(15, 180),
                suggested_price=round(random.uniform(800, 3500), 2),
                suggested_currency="EUR",
            )
            session.add(m)
            matches.append(m)
        await session.flush()
        print(f"  ✅ {len(matches)} demo matches created")

    # ── 8. One booked deal ─────────────────────────────────────────────────────
    deal_exists = await session.scalar(select(Deal).limit(1))
    if not deal_exists and loads and vehicles:
        load = loads[0]
        vehicle = vehicles[0]
        carrier_rec_res = await session.scalar(select(Carrier).limit(1))
        if carrier_rec_res:
            deal = Deal(
                load_id=load.id,
                vehicle_id=vehicle.id,
                carrier_id=carrier_rec_res.id,
                status=DealStatus.IN_TRANSIT,
                price_amount=1200.00,
                price_currency="EUR",
            )
            session.add(deal)
            load.status = LoadStatus.BOOKED
            await session.flush()
            print("  ✅ 1 demo deal (in_transit) created")

    await session.commit()
    print("\n✅ Demo seed complete!")
    print(f"\n📋 Login credentials (password for all: {DEMO_PASSWORD})")
    print("  admin@demo.trucklink.app        → Admin")
    print("  shipper1@demo.trucklink.app     → Shipper (вантажовідправник)")
    print("  shipper2@demo.trucklink.app     → Shipper (вантажовідправник)")
    print("  carrier1@demo.trucklink.app     → Carrier (перевізник)")
    print("  carrier2@demo.trucklink.app     → Carrier (перевізник)")
    print("  forwarder1@demo.trucklink.app   → Forwarder (експедитор)")


async def drop_demo(session: AsyncSession) -> None:
    print("🗑  Dropping all demo data...")
    demo_emails = (
        ["admin@demo.trucklink.app"]
        + [s["email"] for s in SHIPPERS]
        + [c["email"] for c in CARRIERS]
        + [f["email"] for f in FORWARDERS]
    )
    # Get company ids first
    users = (await session.scalars(
        select(User).where(User.email.in_(demo_emails))
    )).all()
    company_ids = [u.company_id for u in users if u.company_id]

    # Delete loads with source=platform and external_id starting with DEMO-
    await session.execute(text(
        "DELETE FROM lera_logistics.loads WHERE source='platform' AND external_id LIKE 'DEMO-%'"
    ))
    # Delete vehicles
    if company_ids:
        await session.execute(text(
            f"DELETE FROM lera_logistics.vehicles WHERE company_id = ANY(ARRAY{company_ids}::int[])"
        ))
        await session.execute(text(
            f"DELETE FROM lera_logistics.users WHERE company_id = ANY(ARRAY{company_ids}::int[])"
        ))
        await session.execute(text(
            f"DELETE FROM lera_logistics.companies WHERE id = ANY(ARRAY{company_ids}::int[])"
        ))
    await session.commit()
    print("✅ Demo data removed.")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true", help="Remove demo data instead of seeding")
    args = parser.parse_args()

    engine = create_async_engine(
        _settings.database_url,
        echo=False,
        execution_options={"schema_translate_map": {None: _settings.db_schema}},
    )
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        if args.drop:
            await drop_demo(session)
        else:
            await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
