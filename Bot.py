import asyncio
import random
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import closing

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_БОТА")

ADMIN_ID = 8465432674

DATABASE_URL = os.getenv("DATABASE_URL")

NORMAL_CASE_PRICE = 1000
PREMIUM_CASE_PRICE = 10000

RACE_BETS = [
    500,
    1000,
    5000,
    10000
]

MAX_CAR_LEVEL = 10


# =========================================================
# BOT
# =========================================================

bot = Bot(TOKEN)

dp = Dispatcher(
    storage=MemoryStorage()
)


# =========================================================
# FSM
# =========================================================

class RaceStates(StatesGroup):
    waiting_code = State()


class AdminStates(StatesGroup):
    waiting_money = State()
    waiting_broadcast = State()


# =========================================================
# DATABASE
# =========================================================

db = None

def _sql(sql):
    return sql.replace("?", "%s")


def execute(sql, params=()):
    global db
    if db is None:
        raise RuntimeError("DATABASE_URL не настроен")
    try:
        with closing(db.cursor(cursor_factory=RealDictCursor)) as cur:
            cur.execute(_sql(sql), params)
            db.commit()
            return cur
    except Exception:
        db.rollback()
        raise


def fetchone(sql, params=()):
    global db
    if db is None:
        raise RuntimeError("DATABASE_URL не настроен")
    with closing(db.cursor(cursor_factory=RealDictCursor)) as cur:
        cur.execute(_sql(sql), params)
        return cur.fetchone()


def fetchall(sql, params=()):
    global db
    if db is None:
        raise RuntimeError("DATABASE_URL не настроен")
    with closing(db.cursor(cursor_factory=RealDictCursor)) as cur:
        cur.execute(_sql(sql), params)
        return cur.fetchall()


def init_database():
    global db
    if not DATABASE_URL:
        raise RuntimeError("Не задана переменная DATABASE_URL в Railway")

    db = psycopg2.connect(DATABASE_URL, sslmode="require")
    db.autocommit = False

    execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT DEFAULT '',
        money BIGINT DEFAULT 5000,
        race_wins INTEGER DEFAULT 0,
        race_games INTEGER DEFAULT 0
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS cars (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        name TEXT NOT NULL,
        rarity TEXT NOT NULL,
        power INTEGER NOT NULL,
        price BIGINT NOT NULL,
        level INTEGER DEFAULT 1,
        exclusive BOOLEAN DEFAULT FALSE
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS races (
        code VARCHAR(4) PRIMARY KEY,
        player1 BIGINT NOT NULL,
        player2 BIGINT,
        car1 BIGINT,
        car2 BIGINT,
        bet BIGINT NOT NULL,
        running BOOLEAN DEFAULT FALSE
    )
    """)

    # Совместимость с уже созданной PostgreSQL БД.
    for column, definition in [
        ("username", "TEXT DEFAULT ''"),
        ("money", "BIGINT DEFAULT 5000"),
        ("race_wins", "INTEGER DEFAULT 0"),
        ("race_games", "INTEGER DEFAULT 0"),
    ]:
        row = fetchone("""SELECT 1 FROM information_schema.columns
                         WHERE table_schema='public' AND table_name='users' AND column_name=?""", (column,))
        if not row:
            execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    # Если старая версия использовала balance, переносим его в money.
    balance_col = fetchone("""SELECT 1 FROM information_schema.columns
                            WHERE table_schema='public' AND table_name='users' AND column_name='balance'""")
    if balance_col:
        execute("UPDATE users SET money=balance WHERE money=5000 AND balance<>5000")

    # После перезапуска незавершённые гонки не должны зависнуть.
    stale = fetchall("SELECT player1, player2, bet FROM races WHERE running=TRUE")
    for race in stale:
        execute("UPDATE users SET money=money+? WHERE user_id=?", (race["bet"], race["player1"]))
        if race["player2"]:
            execute("UPDATE users SET money=money+? WHERE user_id=?", (race["bet"], race["player2"]))
    execute("DELETE FROM races WHERE running=TRUE")

    for race in fetchall("SELECT code, player1, player2, car1, car2, bet, running FROM races"):
        race_rooms[race["code"]] = dict(race)
        player_race[race["player1"]] = race["code"]
        if race["player2"]:
            player_race[race["player2"]] = race["code"]


# =========================================================
# МАШИНЫ
# =========================================================

CARS = [

    ("Lada Granta", "⚪ Обычная", 80, 5000),
    ("Lada Vesta", "⚪ Обычная", 100, 7000),
    ("Renault Logan", "⚪ Обычная", 90, 6000),
    ("Volkswagen Polo", "⚪ Обычная", 110, 9000),
    ("Kia Rio", "⚪ Обычная", 115, 10000),
    ("Hyundai Solaris", "⚪ Обычная", 120, 11000),

    ("Toyota Camry", "🟢 Редкая", 180, 25000),
    ("BMW 320i", "🟢 Редкая", 190, 28000),
    ("Mercedes C200", "🟢 Редкая", 200, 30000),
    ("Audi A4", "🟢 Редкая", 210, 32000),
    ("Honda Civic Type R", "🟢 Редкая", 230, 35000),

    ("BMW M3", "🔵 Эпическая", 350, 60000),
    ("Mercedes AMG C63", "🔵 Эпическая", 370, 65000),
    ("Audi RS5", "🔵 Эпическая", 380, 70000),
    ("Nissan GT-R", "🔵 Эпическая", 400, 80000),
    ("Toyota Supra", "🔵 Эпическая", 390, 75000),

    ("Porsche 911 Turbo", "🟣 Легендарная", 550, 150000),
    ("Lamborghini Huracan", "🟣 Легендарная", 600, 180000),
    ("Ferrari 488", "🟣 Легендарная", 620, 200000),
    ("McLaren 720S", "🟣 Легендарная", 650, 220000),

    ("Lamborghini Aventador", "🟡 Мифическая", 800, 350000),
    ("Bugatti Chiron", "🟡 Мифическая", 900, 500000),
    ("Koenigsegg Jesko", "🟡 Мифическая", 1000, 700000),
    ("Pagani Huayra", "🟡 Мифическая", 950, 600000),

    ("Bugatti Bolide", "🔴 Ультра", 1200, 1000000),
    ("Koenigsegg Gemera", "🔴 Ультра", 1100, 900000)
]


EXCLUSIVE_CARS = [

    ("👑 Bugatti La Voiture Noire", "💎 ЭКСКЛЮЗИВ", 1500, 3000000),
    ("🔥 Lamborghini Veneno", "💎 ЭКСКЛЮЗИВ", 1400, 2500000),
    ("⚡ Koenigsegg One:1", "💎 ЭКСКЛЮЗИВ", 1600, 3500000),
    ("🌌 Pagani Zonda HP Barchetta", "💎 ЭКСКЛЮЗИВ", 1450, 2800000),
    ("👽 McLaren Solus GT", "💎 ЭКСКЛЮЗИВ", 1700, 4000000),
    ("☠️ Black Phantom", "💎 ЭКСКЛЮЗИВ", 2000, 5000000)
]


NORMAL_WEIGHTS = {
    "⚪ Обычная": 55,
    "🟢 Редкая": 27,
    "🔵 Эпическая": 12,
    "🟣 Легендарная": 5,
    "🟡 Мифическая": 1
}


PREMIUM_WEIGHTS = {
    "🟢 Редкая": 35,
    "🔵 Эпическая": 35,
    "🟣 Легендарная": 20,
    "🟡 Мифическая": 9,
    "🔴 Ультра": 1
}


# =========================================================
# АКТИВНЫЕ ГОНКИ
# =========================================================

race_rooms = {}

# user_id -> race_code
player_race = {}


# PostgreSQL и активные гонки инициализируются после объявления race_rooms.
init_database()


# =========================================================
# USERS
# =========================================================

def ensure_user(
    user_id,
    username=""
):

    execute("""
    INSERT OR IGNORE INTO users
    (
        user_id,
        username,
        money,
        race_wins,
        race_games
    )
    VALUES (?, ?, 5000, 0, 0)
    """, (
        user_id,
        username or ""
    ))

    execute("""
    UPDATE users
    SET username=?
    WHERE user_id=?
    """, (
        username or "",
        user_id
    ))


def get_money(
    user_id
):

    row = fetchone("""
    SELECT money
    FROM users
    WHERE user_id=?
    """, (
        user_id,
    ))

    return row["money"] if row else 0


def change_money(
    user_id,
    amount
):

    execute("""
    UPDATE users
    SET money=money+?
    WHERE user_id=?
    """, (
        amount,
        user_id
    ))


def take_money(
    user_id,
    amount
):

    if amount <= 0:
        return False

    row = fetchone("""
    UPDATE users
    SET money=money-?
    WHERE user_id=? AND money>=?
    RETURNING user_id
    """, (amount, user_id, amount))

    return bool(row)


def add_race_game(
    user_id
):

    execute("""
    UPDATE users
    SET race_games=race_games+1
    WHERE user_id=?
    """, (
        user_id,
    ))


def add_race_win(
    user_id
):

    execute("""
    UPDATE users
    SET race_wins=race_wins+1
    WHERE user_id=?
    """, (
        user_id,
    ))


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def main_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🎁 Кейсы"),
                KeyboardButton(text="🚗 Гараж")
            ],
            [
                KeyboardButton(text="🏁 Гонки"),
                KeyboardButton(text="💰 Баланс")
            ]
        ],
        resize_keyboard=True
    )


def admin_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👥 Игроки"),
                KeyboardButton(text="📊 Статистика")
            ],
            [
                KeyboardButton(text="💰 Выдать деньги"),
                KeyboardButton(text="👑 Эксклюзивные машины")
            ],
            [
                KeyboardButton(text="📢 Рассылка"),
                KeyboardButton(text="🧹 Очистить гонки")
            ],
            [
                KeyboardButton(text="❌ Выйти")
            ]
        ],
        resize_keyboard=True
    )


# =========================================================
# START
# =========================================================

@dp.message(Command("start"))
async def cmd_start(
    message: types.Message
):

    ensure_user(
        message.from_user.id,
        message.from_user.username
    )

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) == 2:

        payload = parts[1]

        if payload.startswith("race_"):

            code = payload[5:]

            await join_race(
                message.from_user.id,
                code,
                message
            )

            return

    await message.answer(

        "🚗 <b>CAR CASE</b>\n\n"
        "🎁 Открывай кейсы\n"
        "🚗 Собирай машины\n"
        "⬆️ Улучшай автомобили\n"
        "💰 Продавай машины\n"
        "🏁 Играй в гонки\n"
        "🏆 Поднимайся в топе\n\n"
        "Выбирай действие 👇",

        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================================================
# BALANCE
# =========================================================

@dp.message(F.text == "💰 Баланс")
async def balance(
    message: types.Message
):

    user_id = message.from_user.id

    ensure_user(
        user_id,
        message.from_user.username
    )

    row = fetchone("""
    SELECT money, race_wins, race_games
    FROM users
    WHERE user_id=?
    """, (
        user_id,
    ))

    await message.answer(

        "💰 <b>ПРОФИЛЬ</b>\n\n"
        f"💵 Баланс: <b>{row['money']:,}$</b>\n"
        f"🏁 Гонок: {row['race_games']}\n"
        f"🏆 Побед: {row['race_wins']}"

        .replace(",", " "),

        parse_mode="HTML"
    )


# =========================================================
# CASES
# =========================================================

def cases_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎁 Обычный — 1 000$",
                    callback_data="case:normal"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 Премиум — 10 000$",
                    callback_data="case:premium"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть",
                    callback_data="delete"
                )
            ]
        ]
    )


@dp.message(F.text == "🎁 Кейсы")
async def cases(
    message: types.Message
):

    ensure_user(
        message.from_user.id,
        message.from_user.username
    )

    await message.answer(

        "🎁 <b>КЕЙСЫ</b>\n\n"
        "🎁 Обычный — 1 000$\n"
        "💎 Премиум — 10 000$\n\n"
        "Выбирай:",

        parse_mode="HTML",
        reply_markup=cases_keyboard()
    )


def random_car(
    weights
):

    rarity = random.choices(
        list(weights.keys()),
        weights=list(weights.values()),
        k=1
    )[0]

    cars = [
        car for car in CARS
        if car[1] == rarity
    ]

    return random.choice(cars)


@dp.callback_query(F.data.startswith("case:"))
async def open_case(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    kind = callback.data.split(":")[1]

    if kind == "normal":

        price = NORMAL_CASE_PRICE
        weights = NORMAL_WEIGHTS
        title = "🎁 ОБЫЧНЫЙ КЕЙС"

    else:

        price = PREMIUM_CASE_PRICE
        weights = PREMIUM_WEIGHTS
        title = "💎 ПРЕМИУМ КЕЙС"

    if not take_money(
        user_id,
        price
    ):

        await callback.answer(
            "❌ Недостаточно денег!",
            show_alert=True
        )

        return

    name, rarity, power, car_price = random_car(
        weights
    )

    execute("""
    INSERT INTO cars
    (
        user_id,
        name,
        rarity,
        power,
        price,
        level,
        exclusive
    )
    VALUES (?, ?, ?, ?, ?, 1, 0)
    """, (
        user_id,
        name,
        rarity,
        power,
        car_price
    ))

    await callback.message.edit_text(

        f"🎉 <b>{title}</b>\n\n"
        f"🚗 <b>{name}</b>\n"
        f"{rarity}\n\n"
        f"⚡ Мощность: {power}\n"
        f"💰 Цена: {car_price:,}$\n\n"
        "🚗 Машина добавлена в гараж!"

        .replace(",", " "),

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎁 Ещё кейс",
                        callback_data="cases"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🚗 Гараж",
                        callback_data="garage"
                    )
                ]
            ]
        )
    )

    await callback.answer()


@dp.callback_query(F.data == "cases")
async def cases_callback(
    callback: CallbackQuery
):

    await callback.message.edit_text(
        "🎁 <b>КЕЙСЫ</b>\n\nВыбирай:",
        parse_mode="HTML",
        reply_markup=cases_keyboard()
    )


# =========================================================
# GARAGE
# =========================================================

def garage_keyboard(
    user_id
):

    cars = fetchall("""
    SELECT id, name, level, exclusive
    FROM cars
    WHERE user_id=?
    ORDER BY id DESC
    """, (
        user_id,
    ))

    buttons = []

    for car in cars:

        prefix = "👑 " if car["exclusive"] else "🚗 "

        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"{prefix}{car['name']} "
                    f"• ⭐{car['level']}"
                ),
                callback_data=f"car:{car['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="❌ Закрыть",
            callback_data="delete"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


async def show_garage(
    message,
    user_id,
    edit=False
):

    cars = fetchall("""
    SELECT id
    FROM cars
    WHERE user_id=?
    """, (
        user_id,
    ))

    if not cars:

        text = (
            "🚗 <b>ГАРАЖ ПУСТ</b>\n\n"
            "Открой кейс!"
        )

        if edit:
            await message.edit_text(
                text,
                parse_mode="HTML"
            )
        else:
            await message.answer(
                text,
                parse_mode="HTML"
            )

        return

    text = (
        "🚗 <b>ВАШ ГАРАЖ</b>\n\n"
        "Выберите машину:"
    )

    if edit:

        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=garage_keyboard(user_id)
        )

    else:

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=garage_keyboard(user_id)
        )


@dp.message(F.text == "🚗 Гараж")
async def garage(
    message: types.Message
):

    ensure_user(
        message.from_user.id,
        message.from_user.username
    )

    await show_garage(
        message,
        message.from_user.id
    )


@dp.callback_query(F.data == "garage")
async def garage_callback(
    callback: CallbackQuery
):

    await show_garage(
        callback.message,
        callback.from_user.id,
        True
    )

    await callback.answer()


# =========================================================
# CAR
# =========================================================

def car_keyboard(
    car_id
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬆️ Улучшить",
                    callback_data=f"upgrade:{car_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Продать",
                    callback_data=f"sell:{car_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏁 В гонку",
                    callback_data=f"racecar:{car_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Гараж",
                    callback_data="garage"
                )
            ]
        ]
    )


@dp.callback_query(F.data.startswith("car:"))
async def show_car(
    callback: CallbackQuery
):

    car_id = int(
        callback.data.split(":")[1]
    )

    car = fetchone("""
    SELECT *
    FROM cars
    WHERE id=?
    AND user_id=?
    """, (
        car_id,
        callback.from_user.id
    ))

    if not car:

        await callback.answer(
            "❌ Машина не найдена.",
            show_alert=True
        )

        return

    special = (
        "\n👑 <b>ЭКСКЛЮЗИВ</b>\n"
        if car["exclusive"]
        else ""
    )

    if car["level"] >= MAX_CAR_LEVEL:

        upgrade = "⭐ Максимальный уровень"

    else:

        upgrade_price = 1000 * car["level"]

        upgrade = (
            f"⬆️ Улучшение: "
            f"{upgrade_price:,}$"
        ).replace(",", " ")

    await callback.message.edit_text(

        f"🚗 <b>{car['name']}</b>\n"
        f"{special}"
        f"{car['rarity']}\n\n"
        f"⚡ Мощность: {car['power']}\n"
        f"⭐ Уровень: {car['level']}/{MAX_CAR_LEVEL}\n"
        f"💰 Цена: {car['price']:,}$\n\n"
        f"{upgrade}"

        .replace(",", " "),

        parse_mode="HTML",
        reply_markup=car_keyboard(car_id)
    )

    await callback.answer()


# =========================================================
# UPGRADE
# =========================================================

@dp.callback_query(F.data.startswith("upgrade:"))
async def upgrade_car(
    callback: CallbackQuery
):

    car_id = int(
        callback.data.split(":")[1]
    )

    car = fetchone("""
    SELECT *
    FROM cars
    WHERE id=?
    AND user_id=?
    """, (
        car_id,
        callback.from_user.id
    ))

    if not car:

        await callback.answer(
            "❌ Машина не найдена.",
            show_alert=True
        )

        return

    if car["level"] >= MAX_CAR_LEVEL:

        await callback.answer(
            "⭐ Максимальный уровень!",
            show_alert=True
        )

        return

    cost = 1000 * car["level"]

    if not take_money(
        callback.from_user.id,
        cost
    ):

        await callback.answer(
            f"❌ Нужно {cost:,}$".replace(",", " "),
            show_alert=True
        )

        return

    bonus = 40 if car["exclusive"] else 20

    new_level = car["level"] + 1
    new_power = car["power"] + bonus

    # ВАЖНО:
    # price НЕ изменяется.
    execute("""
    UPDATE cars
    SET level=?, power=?
    WHERE id=?
    AND user_id=?
    """, (
        new_level,
        new_power,
        car_id,
        callback.from_user.id
    ))

    await callback.answer(
        "⬆️ Машина улучшена!"
    )

    await callback.message.edit_text(

        f"🚗 <b>{car['name']}</b>\n"
        f"{car['rarity']}\n\n"
        f"⚡ Мощность: {new_power}\n"
        f"⭐ Уровень: {new_level}/{MAX_CAR_LEVEL}\n"
        f"💰 Цена: {car['price']:,}$"

        .replace(",", " "),

        parse_mode="HTML",
        reply_markup=car_keyboard(car_id)
    )


# =========================================================
# SELL
# =========================================================

@dp.callback_query(F.data.startswith("sell:"))
async def sell_car(
    callback: CallbackQuery
):

    car_id = int(
        callback.data.split(":")[1]
    )

    car = fetchone("""
    SELECT name, price
    FROM cars
    WHERE id=?
    AND user_id=?
    """, (
        car_id,
        callback.from_user.id
    ))

    if not car:

        await callback.answer(
            "❌ Машина не найдена.",
            show_alert=True
        )

        return

    sell_price = int(
        car["price"] * 0.8
    )

    execute("""
    DELETE FROM cars
    WHERE id=?
    AND user_id=?
    """, (
        car_id,
        callback.from_user.id
    ))

    change_money(
        callback.from_user.id,
        sell_price
    )

    await callback.message.edit_text(

        "💰 <b>МАШИНА ПРОДАНА</b>\n\n"
        f"🚗 {car['name']}\n"
        f"💵 Получено: {sell_price:,}$"

        .replace(",", " "),

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚗 Гараж",
                        callback_data="garage"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# =========================================================
# RACE MENU
# =========================================================

def race_menu_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏁 Создать гонку",
                    callback_data="race:create"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Войти по коду",
                    callback_data="race:join"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏆 Топ победителей",
                    callback_data="race:top"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть",
                    callback_data="delete"
                )
            ]
        ]
    )


@dp.message(F.text == "🏁 Гонки")
async def races(
    message: types.Message
):

    if message.from_user.id in player_race:

        await message.answer(
            "❌ Вы уже участвуете в гонке."
        )

        return

    await message.answer(

        "🏁 <b>ГОНКИ</b>\n\n"
        "💰 Игра на свои деньги.\n"
        "🏆 Победитель получает банк.\n"
        "🤝 При ничьей ставки возвращаются.\n\n"
        "🔢 Можно войти по коду.\n"
        "🔗 Можно подключиться по ссылке.",

        parse_mode="HTML",
        reply_markup=race_menu_keyboard()
    )


# =========================================================
# CREATE RACE
# =========================================================

def save_race(code):
    room = race_rooms.get(code)
    if not room:
        return
    execute("""
    INSERT INTO races (code, player1, player2, car1, car2, bet, running)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (code) DO UPDATE SET
        player1=EXCLUDED.player1, player2=EXCLUDED.player2,
        car1=EXCLUDED.car1, car2=EXCLUDED.car2,
        bet=EXCLUDED.bet, running=EXCLUDED.running
    """, (code, room["player1"], room["player2"], room["car1"], room["car2"], room["bet"], room["running"]))


def bet_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="500$",
                    callback_data="bet:500"
                ),
                InlineKeyboardButton(
                    text="1 000$",
                    callback_data="bet:1000"
                )
            ],
            [
                InlineKeyboardButton(
                    text="5 000$",
                    callback_data="bet:5000"
                ),
                InlineKeyboardButton(
                    text="10 000$",
                    callback_data="bet:10000"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="race:menu"
                )
            ]
        ]
    )


@dp.callback_query(F.data == "race:create")
async def create_race_menu(
    callback: CallbackQuery
):

    await callback.message.edit_text(

        "🏁 <b>СТАВКА</b>\n\n"
        "Выберите сумму:",

        parse_mode="HTML",
        reply_markup=bet_keyboard()
    )

    await callback.answer()


def generate_code():

    while True:

        code = str(
            random.randint(
                1000,
                9999
            )
        )

        if code not in race_rooms:
            return code


@dp.callback_query(F.data.startswith("bet:"))
async def create_race(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    if user_id in player_race:

        await callback.answer(
            "❌ Вы уже в гонке.",
            show_alert=True
        )

        return

    bet = int(
        callback.data.split(":")[1]
    )

    if bet not in RACE_BETS:

        await callback.answer(
            "❌ Недопустимая ставка.",
            show_alert=True
        )

        return

    if not take_money(
        user_id,
        bet
    ):

        await callback.answer(
            "❌ Недостаточно денег.",
            show_alert=True
        )

        return

    code = generate_code()

    race_rooms[code] = {
        "player1": user_id,
        "player2": None,
        "car1": None,
        "car2": None,
        "bet": bet,
        "running": False
    }

    player_race[user_id] = code
    save_race(code)

    me = await bot.get_me()

    link = (
        f"https://t.me/"
        f"{me.username}"
        f"?start=race_{code}"
    )

    await callback.message.edit_text(

        "🏁 <b>ГОНКА СОЗДАНА</b>\n\n"
        f"🔢 Код: <code>{code}</code>\n"
        f"💰 Ставка: <b>{bet:,}$</b>\n"
        f"🏆 Банк: <b>{bet * 2:,}$</b>\n\n"
        "Отправьте сопернику код "
        "или ссылку.",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔗 Ссылка",
                        url=link
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🚗 Выбрать машину",
                        callback_data="race:car"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить",
                        callback_data="race:cancel"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# =========================================================
# JOIN BY CODE
# =========================================================

@dp.callback_query(F.data == "race:join")
async def join_code_start(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.set_state(
        RaceStates.waiting_code
    )

    await callback.message.edit_text(

        "🔢 <b>ВХОД В ГОНКУ</b>\n\n"
        "Отправьте 4 цифры кода.\n\n"
        "Например: <code>5821</code>",

        parse_mode="HTML"
    )

    await callback.answer()


@dp.message(RaceStates.waiting_code)
async def join_code_message(
    message: types.Message,
    state: FSMContext
):

    code = message.text.strip()

    await state.clear()

    if (
        not code.isdigit()
        or len(code) != 4
    ):

        await message.answer(
            "❌ Код должен состоять из 4 цифр."
        )

        return

    await join_race(
        message.from_user.id,
        code,
        message
    )


async def join_race(
    user_id,
    code,
    message
):

    ensure_user(
        user_id,
        message.from_user.username
    )

    if user_id in player_race:

        await message.answer(
            "❌ Вы уже участвуете в гонке."
        )

        return

    room = race_rooms.get(code)

    if not room:

        await message.answer(
            "❌ Гонка не найдена."
        )

        return

    if room["player1"] == user_id:

        await message.answer(
            "❌ Нельзя подключиться к своей гонке."
        )

        return

    if room["player2"]:

        await message.answer(
            "❌ В гонке уже есть второй игрок."
        )

        return

    bet = room["bet"]

    if not take_money(
        user_id,
        bet
    ):

        await message.answer(
            f"❌ Нужно {bet:,}$"
            .replace(",", " ")
        )

        return

    room["player2"] = user_id

    player_race[user_id] = code
    save_race(code)

    await message.answer(

        "⚔️ <b>ВЫ ПОДКЛЮЧИЛИСЬ</b>\n\n"
        f"💰 Ставка: {bet:,}$\n"
        f"🏆 Банк: {bet * 2:,}$\n\n"
        "Выберите машину:",

        parse_mode="HTML",
        reply_markup=race_car_keyboard(user_id)
    )

    await bot.send_message(

        room["player1"],

        "⚔️ <b>СОПЕРНИК ПОДКЛЮЧИЛСЯ!</b>\n\n"
        f"💰 Ставка: {bet:,}$\n"
        f"🏆 Банк: {bet * 2:,}$\n\n"
        "Выберите машину:",

        parse_mode="HTML",
        reply_markup=race_car_keyboard(
            room["player1"]
        )
    )


# =========================================================
# RACE CAR SELECTION
# =========================================================

def race_car_keyboard(
    user_id
):

    cars = fetchall("""
    SELECT id, name, level, exclusive
    FROM cars
    WHERE user_id=?
    ORDER BY id DESC
    """, (
        user_id,
    ))

    buttons = []

    for car in cars:

        prefix = "👑 " if car["exclusive"] else "🚗 "

        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"{prefix}{car['name']} "
                    f"• ⭐{car['level']}"
                ),
                callback_data=f"racecar:{car['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data="race:cancel"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


@dp.callback_query(F.data == "race:car")
async def choose_race_car(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    if user_id not in player_race:

        await callback.answer(
            "❌ Гонка не найдена.",
            show_alert=True
        )

        return

    await callback.message.edit_text(

        "🚗 <b>ВЫБЕРИТЕ МАШИНУ</b>",

        parse_mode="HTML",
        reply_markup=race_car_keyboard(user_id)
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("racecar:"))
async def race_car_selected(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    car_id = int(
        callback.data.split(":")[1]
    )

    code = player_race.get(
        user_id
    )

    if not code:

        await callback.answer(
            "❌ Гонка не найдена.",
            show_alert=True
        )

        return

    room = race_rooms.get(code)

    if not room or room["running"]:

        await callback.answer(
            "❌ Гонка уже началась.",
            show_alert=True
        )

        return

    car = fetchone("""
    SELECT name, power, level
    FROM cars
    WHERE id=?
    AND user_id=?
    """, (
        car_id,
        user_id
    ))

    if not car:

        await callback.answer(
            "❌ Машина не найдена.",
            show_alert=True
        )

        return

    if room["player1"] == user_id:

        room["car1"] = car_id

    elif room["player2"] == user_id:

        room["car2"] = car_id

    save_race(code)

    await callback.message.edit_text(

        "🚗 <b>МАШИНА ВЫБРАНА</b>\n\n"
        f"{car['name']}\n"
        f"⚡ {car['power']}\n"
        f"⭐ Уровень {car['level']}\n\n"
        "⏳ Ожидаем соперника...",

        parse_mode="HTML"
    )

    await callback.answer()

    if (
        room["car1"]
        and room["car2"]
        and not room["running"]
    ):

        room["running"] = True
        save_race(code)

        asyncio.create_task(
            run_race(code)
        )


# =========================================================
# CANCEL RACE
# =========================================================

def remove_race(
    code
):

    room = race_rooms.pop(
        code,
        None
    )

    if not room:
        return

    player_race.pop(
        room["player1"],
        None
    )

    if room["player2"]:

        player_race.pop(
            room["player2"],
            None
        )

    execute("DELETE FROM races WHERE code=?", (code,))


@dp.callback_query(F.data == "race:cancel")
async def cancel_race(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    code = player_race.get(
        user_id
    )

    if not code:

        await callback.answer(
            "❌ Гонка не найдена.",
            show_alert=True
        )

        return

    room = race_rooms.get(code)

    if not room:

        player_race.pop(
            user_id,
            None
        )

        return

    # Нельзя отменить уже начавшуюся
    if room["running"]:

        await callback.answer(
            "🏁 Гонка уже началась.",
            show_alert=True
        )

        return

    p1 = room["player1"]
    p2 = room["player2"]
    bet = room["bet"]

    # Возвращаем ставки обоим
    change_money(
        p1,
        bet
    )

    if p2:

        change_money(
            p2,
            bet
        )

        try:

            await bot.send_message(
                p2,
                "❌ Гонка отменена.\n"
                "💰 Ставка возвращена."
            )

        except:
            pass

    remove_race(code)

    await callback.message.edit_text(
        "❌ <b>ГОНКА ОТМЕНЕНА</b>\n\n"
        "💰 Ставки возвращены.",
        parse_mode="HTML"
    )

    await callback.answer()


# =========================================================
# RACE ENGINE
# =========================================================

async def run_race(
    code
):

    room = race_rooms.get(code)

    if not room:
        return

    p1 = room["player1"]
    p2 = room["player2"]

    if not p1 or not p2:
        return

    bet = room["bet"]

    # =====================================================
    # ОТСЧЁТ
    # =====================================================

    for number in (3, 2, 1):

        for user_id in (p1, p2):

            try:

                await bot.send_message(
                    user_id,
                    f"🏁 <b>{number}</b>",
                    parse_mode="HTML"
                )

            except:
                pass

        await asyncio.sleep(1)

    for user_id in (p1, p2):

        try:

            await bot.send_message(
                user_id,
                "🚦 <b>СТАРТ!</b>",
                parse_mode="HTML"
            )

        except:
            pass

    await asyncio.sleep(1)

    car1 = fetchone("""
    SELECT name, power, level
    FROM cars
    WHERE id=?
    AND user_id=?
    """, (
        room["car1"],
        p1
    ))

    car2 = fetchone("""
    SELECT name, power, level
    FROM cars
    WHERE id=?
    AND user_id=?
    """, (
        room["car2"],
        p2
    ))

    if not car1 or not car2:

        change_money(
            p1,
            bet
        )

        change_money(
            p2,
            bet
        )

        for user_id in (p1, p2):

            try:

                await bot.send_message(
                    user_id,
                    "❌ Ошибка машины.\n"
                    "💰 Ставка возвращена."
                )

            except:
                pass

        remove_race(code)

        return

    # =====================================================
    # СИЛА
    # =====================================================

    score1 = (
        car1["power"]
        + car1["level"] * 20
        + random.randint(0, 100)
    )

    score2 = (
        car2["power"]
        + car2["level"] * 20
        + random.randint(0, 100)
    )

    add_race_game(p1)
    add_race_game(p2)

    # =====================================================
    # НИЧЬЯ
    # =====================================================

    if score1 == score2:

        change_money(
            p1,
            bet
        )

        change_money(
            p2,
            bet
        )

        text = (

            "🤝 <b>НИЧЬЯ!</b>\n\n"

            f"🚗 {car1['name']}\n"
            f"⚡ {score1}\n\n"

            f"🚗 {car2['name']}\n"
            f"⚡ {score2}\n\n"

            f"💰 Обоим возвращено "
            f"{bet:,}$"

        ).replace(",", " ")

        for user_id in (p1, p2):

            try:

                await bot.send_message(
                    user_id,
                    text,
                    parse_mode="HTML"
                )

            except:
                pass

        remove_race(code)

        return

    # =====================================================
    # ПОБЕДИТЕЛЬ
    # =====================================================

    if score1 > score2:

        winner = p1
        loser = p2

        winner_car = car1
        loser_car = car2

        winner_score = score1
        loser_score = score2

    else:

        winner = p2
        loser = p1

        winner_car = car2
        loser_car = car1

        winner_score = score2
        loser_score = score1

    prize = bet * 2

    change_money(
        winner,
        prize
    )

    add_race_win(
        winner
    )

    winner_user = fetchone("""
    SELECT username
    FROM users
    WHERE user_id=?
    """, (
        winner,
    ))

    if (
        winner_user
        and winner_user["username"]
    ):

        winner_name = (
            "@"
            + winner_user["username"]
        )

    else:

        winner_name = "Игрок"

    winner_text = (

        "🏆 <b>ПОБЕДА!</b>\n\n"

        f"🚗 Ваша машина: "
        f"{winner_car['name']}\n"

        f"⚡ Результат: "
        f"{winner_score}\n\n"

        f"🚗 Машина соперника: "
        f"{loser_car['name']}\n"

        f"⚡ Результат соперника: "
        f"{loser_score}\n\n"

        f"💰 Ставка: "
        f"{bet:,}$\n"

        f"🏆 Вы получили: "
        f"<b>{prize:,}$</b>"

    ).replace(",", " ")

    loser_text = (

        "😔 <b>ПОРАЖЕНИЕ</b>\n\n"

        f"🚗 Ваша машина: "
        f"{loser_car['name']}\n"

        f"⚡ Результат: "
        f"{loser_score}\n\n"

        f"🏆 Победитель: "
        f"{winner_name}\n"

        f"🚗 Машина: "
        f"{winner_car['name']}\n"

        f"⚡ Результат: "
        f"{winner_score}\n\n"

        f"💰 Потеряно: "
        f"{bet:,}$"

    ).replace(",", " ")

    try:

        await bot.send_message(
            winner,
            winner_text,
            parse_mode="HTML"
        )

    except:
        pass

    try:

        await bot.send_message(
            loser,
            loser_text,
            parse_mode="HTML"
        )

    except:
        pass

    remove_race(code)


# =========================================================
# TOP
# =========================================================

def top_text():

    users = fetchall("""
    SELECT username, race_wins, race_games
    FROM users
    ORDER BY race_wins DESC, race_games ASC
    LIMIT 10
    """)

    text = "🏆 <b>ТОП ГОНЩИКОВ</b>\n\n"

    if not users:

        return text + "Пока никого нет."

    medals = [
        "🥇",
        "🥈",
        "🥉"
    ]

    for index, user in enumerate(users):

        if user["username"]:

            name = "@" + user["username"]

        else:

            name = "Игрок"

        place = (
            medals[index]
            if index < 3
            else f"{index + 1}."
        )

        text += (
            f"{place} <b>{name}</b>\n"
            f"🏆 Побед: {user['race_wins']}\n"
            f"🏁 Гонок: {user['race_games']}\n\n"
        )

    return text


@dp.callback_query(F.data == "race:top")
async def race_top(
    callback: CallbackQuery
):

    await callback.message.edit_text(

        top_text(),

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Обновить",
                        callback_data="race:top"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="race:menu"
                    )
                ]
            ]
        )
    )

    await callback.answer()


@dp.callback_query(F.data == "race:menu")
async def race_menu(
    callback: CallbackQuery
):

    await callback.message.edit_text(

        "🏁 <b>ГОНКИ</b>\n\n"
        "💰 Игра на деньги\n"
        "🏆 Топ победителей\n"
        "🔢 Подключение по коду\n"
        "🔗 Подключение по ссылке",

        parse_mode="HTML",
        reply_markup=race_menu_keyboard()
    )

    await callback.answer()


# =========================================================
# DELETE INLINE
# =========================================================

@dp.callback_query(F.data == "delete")
async def delete_message(
    callback: CallbackQuery
):

    try:

        await callback.message.delete()

    except:
        pass

    await callback.answer()


# =========================================================
# ADMIN
# =========================================================

@dp.message(Command("admin"))
async def admin_command(
    message: types.Message
):

    if message.from_user.id != ADMIN_ID:

        await message.answer(
            "❌ Доступ запрещён."
        )

        return

    await message.answer(

        "👑 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выберите действие:",

        parse_mode="HTML",
        reply_markup=admin_menu()
    )


# =========================================================
# ADMIN PLAYERS
# =========================================================

@dp.message(F.text == "👥 Игроки")
async def admin_players(
    message: types.Message
):

    if message.from_user.id != ADMIN_ID:
        return

    row = fetchone("""
    SELECT COUNT(*) AS count
    FROM users
    """)

    await message.answer(
        f"👥 Игроков: <b>{row['count']}</b>",
        parse_mode="HTML"
    )


# =========================================================
# ADMIN STATS
# =========================================================

@dp.message(F.text == "📊 Статистика")
async def admin_stats(
    message: types.Message
):

    if message.from_user.id != ADMIN_ID:
        return

    users = fetchone("""
    SELECT COUNT(*) AS value
    FROM users
    """)["value"]

    cars = fetchone("""
    SELECT COUNT(*) AS value
    FROM cars
    """)["value"]

    exclusive = fetchone("""
    SELECT COUNT(*) AS value
    FROM cars
    WHERE exclusive=1
    """)["value"]

    wins = fetchone("""
    SELECT COALESCE(
        SUM(race_wins),
        0
    ) AS value
    FROM users
    """)["value"]

    money = fetchone("""
    SELECT COALESCE(
        SUM(money),
        0
    ) AS value
    FROM users
    """)["value"]

    await message.answer(

        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"👥 Игроков: {users}\n"
        f"🚗 Машин: {cars}\n"
        f"👑 Эксклюзивов: {exclusive}\n"
        f"🏆 Побед: {wins}\n"
        f"💰 Денег: {money:,}$"

        .replace(",", " "),

        parse_mode="HTML"
    )


# =========================================================
# ADMIN GIVE MONEY
# =========================================================

@dp.message(F.text == "💰 Выдать деньги")
async def admin_money_start(
    message: types.Message,
    state: FSMContext
):

    if message.from_user.id != ADMIN_ID:
        return

    await state.set_state(
        AdminStates.waiting_money
    )

    await message.answer(

        "💰 <b>ВЫДАЧА ДЕНЕГ</b>\n\n"
        "Отправьте:\n"
        "<code>ID сумма</code>\n\n"
        "Например:\n"
        "<code>123456789 50000</code>",

        parse_mode="HTML"
    )


@dp.message(AdminStates.waiting_money)
async def admin_money_process(
    message: types.Message,
    state: FSMContext
):

    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    await state.clear()

    try:

        parts = message.text.split()

        if len(parts) != 2:
            raise ValueError

        user_id = int(parts[0])
        amount = int(parts[1])

        if amount <= 0:
            raise ValueError

    except:

        await message.answer(
            "❌ Неверный формат."
        )

        return

    ensure_user(
        user_id
    )

    change_money(
        user_id,
        amount
    )

    await message.answer(

        "✅ <b>ДЕНЬГИ ВЫДАНЫ</b>\n\n"
        f"👤 ID: <code>{user_id}</code>\n"
        f"💰 +{amount:,}$"

        .replace(",", " "),

        parse_mode="HTML"
    )


# =========================================================
# ADMIN EXCLUSIVE
# =========================================================

def exclusive_keyboard():

    buttons = []

    for index, car in enumerate(
        EXCLUSIVE_CARS
    ):

        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"{car[0]} "
                    f"• ⚡{car[2]}"
                ),
                callback_data=f"excar:{index}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="❌ Закрыть",
            callback_data="delete"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


@dp.message(F.text == "👑 Эксклюзивные машины")
async def admin_exclusive(
    message: types.Message
):

    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(

        "👑 <b>ЭКСКЛЮЗИВНЫЕ МАШИНЫ</b>\n\n"
        "Выберите машину для выдачи:",

        parse_mode="HTML",
        reply_markup=exclusive_keyboard()
    )


@dp.callback_query(F.data.startswith("excar:"))
async def exclusive_choose(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "❌ Доступ запрещён.",
            show_alert=True
        )

        return

    index = int(
        callback.data.split(":")[1]
    )

    car = EXCLUSIVE_CARS[index]

    users = fetchall("""
    SELECT user_id, username
    FROM users
    ORDER BY user_id DESC
    LIMIT 50
    """)


    buttons = []

    for user in users:

        name = (
            "@"
            + user["username"]
            if user["username"]
            else "Игрок"
        )

        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {name}",
                callback_data=(
                    f"giveex:{index}:{user['user_id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="exback"
        )
    ])

    await callback.message.edit_text(

        "👑 <b>ВЫДАЧА МАШИНЫ</b>\n\n"
        f"🚗 {car[0]}\n"
        f"⚡ Мощность: {car[2]}\n"
        f"💰 Цена: {car[3]:,}$\n\n"
        "Выберите игрока:"

        .replace(",", " "),

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("giveex:"))
async def give_exclusive(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "❌ Доступ запрещён.",
            show_alert=True
        )

        return

    parts = callback.data.split(":")

    car_index = int(parts[1])
    user_id = int(parts[2])

    name, rarity, power, price = EXCLUSIVE_CARS[
        car_index
    ]

    ensure_user(
        user_id
    )

    execute("""
    INSERT INTO cars
    (
        user_id,
        name,
        rarity,
        power,
        price,
        level,
        exclusive
    )
    VALUES (?, ?, ?, ?, ?, 1, 1)
    """, (
        user_id,
        name,
        rarity,
        power,
        price
    ))

    await callback.message.edit_text(

        "✅ <b>МАШИНА ВЫДАНА</b>\n\n"
        f"🚗 {name}\n"
        f"⚡ {power}\n"
        f"💰 {price:,}$"

        .replace(",", " "),

        parse_mode="HTML"
    )

    try:

        await bot.send_message(

            user_id,

            "👑 <b>ВАМ ВЫДАЛИ ЭКСКЛЮЗИВНУЮ МАШИНУ!</b>\n\n"
            f"🚗 {name}\n"
            f"💎 {rarity}\n"
            f"⚡ {power}\n"
            f"💰 {price:,}$"

            .replace(",", " "),

            parse_mode="HTML"
        )

    except:
        pass

    await callback.answer(
        "👑 Выдано!"
    )


@dp.callback_query(F.data == "exback")
async def exclusive_back(
    callback: CallbackQuery
):

    await callback.message.edit_text(

        "👑 <b>ЭКСКЛЮЗИВНЫЕ МАШИНЫ</b>\n\n"
        "Выберите машину:",

        parse_mode="HTML",
        reply_markup=exclusive_keyboard()
    )

    await callback.answer()


# =========================================================
# ADMIN BROADCAST
# =========================================================

@dp.message(F.text == "📢 Рассылка")
async def admin_broadcast_start(
    message: types.Message,
    state: FSMContext
):

    if message.from_user.id != ADMIN_ID:
        return

    await state.set_state(
        AdminStates.waiting_broadcast
    )

    await message.answer(
        "📢 Отправьте текст рассылки."
    )


@dp.message(AdminStates.waiting_broadcast)
async def admin_broadcast_process(
    message: types.Message,
    state: FSMContext
):

    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    await state.clear()

    users = fetchall("""
    SELECT user_id
    FROM users
    """)

    success = 0

    for user in users:

        try:

            await bot.send_message(
                user["user_id"],
                "📢 <b>СООБЩЕНИЕ ОТ АДМИНИСТРАЦИИ</b>\n\n"
                + message.text,
                parse_mode="HTML"
            )

            success += 1

            await asyncio.sleep(
                0.04
            )

        except:
            pass

    await message.answer(
        f"✅ Рассылка завершена.\n"
        f"📨 Отправлено: {success}"
    )


# =========================================================
# ADMIN CLEAR RACES
# =========================================================

@dp.message(F.text == "🧹 Очистить гонки")
async def admin_clear_races(
    message: types.Message
):

    if message.from_user.id != ADMIN_ID:
        return

    count = 0

    for code, room in list(
        race_rooms.items()
    ):

        change_money(
            room["player1"],
            room["bet"]
        )

        if room["player2"]:

            change_money(
                room["player2"],
                room["bet"]
            )

        count += 1

    race_rooms.clear()
    player_race.clear()

    await message.answer(
        f"🧹 Очищено гонок: {count}\n"
        "💰 Все ставки возвращены."
    )


# =========================================================
# ADMIN EXIT
# =========================================================

@dp.message(F.text == "❌ Выйти")
async def admin_exit(
    message: types.Message,
    state: FSMContext
):

    if message.from_user.id != ADMIN_ID:
        return

    await state.clear()

    await message.answer(
        "✅ Вы вышли из админ-панели.",
        reply_markup=main_menu()
    )


# =========================================================
# FALLBACK
# =========================================================

@dp.message()
async def fallback(
    message: types.Message
):

    if message.text.startswith("/"):
        return

    await message.answer(
        "Выберите действие в меню 👇",
        reply_markup=main_menu()
    )


# =========================================================
# RUN
# =========================================================

async def main():

    if not TOKEN or TOKEN == "ВСТАВЬ_ТОКЕН_БОТА":
        raise RuntimeError("Задай BOT_TOKEN в Railway Variables")

    print(
        "================================"
    )

    print(
        "🚗 CAR CASE BOT"
    )

    print(
        "✅ Бот запущен"
    )

    print(
        "================================"
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(
        main()
  )
