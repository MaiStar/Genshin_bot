### -------------------------------------------------###

# pip install aiogram
# Для хранения данных пока используем JSON файл (users.json).
# Позже можно перейти на SQLite: простая БД для Telegram ботов.
# Пример схемы для SQLite:
# CREATE TABLE users (user_id INTEGER PRIMARY KEY, name TEXT, timezone INTEGER, expedition_end REAL);  # end as unix timestamp
# Для уведомлений: используем asyncio task для периодической проверки (каждую минуту).
# Это не идеально для production (лучше APScheduler или Celery), но для старта ок.
# Часовой пояс: храним как offset от UTC в часах (e.g., 3 для +3).
# Проверка имени: длина 1-50, без SQL-опасных символов (--, ';', etc.).
# Время экспедиции: /exp4, /exp8, /exp12, /exp20 — устанавливает конец в UTC.
# Уведомление: "Привет, [name]! Экспедиция в Genshin завершена. Время по твоему поясу: [local_time]."

# @BotFather
# GenshinExpedition_bot
# BOT_TOKEN = 'your_token_here'
# python3 -m venv myenv
# source myenv/bin/activate
# deactivate
# pip freeze > requirements.txt
# pip install -r requirements.txt

### -------------------------------------------------###

import time
import asyncio
import logging
import json
import os
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Настраиваем логирование
logging.basicConfig(
    level=logging.DEBUG,  # Установите DEBUG для максимального логирования
    format="%(asctime)s - %(levelname)s - %(message)s",
)

BOT_TOKEN = '8222455312:AAHESEtp3av-tmtLmXNjCZH15sTVI38pe6A'

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

USERS_FILE = "users.json"
os.makedirs(os.path.dirname(USERS_FILE) if os.path.dirname(
    USERS_FILE) else '.', exist_ok=True)

# Загрузка/сохранение пользователей


def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


users = load_users()
logging.info("Бот запущен и готов к работе!")


class UserStates(StatesGroup):
    waiting_name = State()
    waiting_timezone = State()

# Проверка имени на безопасность (длина и базовая защита от инъекций)


def validate_name(name: str) -> tuple[bool, str]:
    if not name or len(name.strip()) < 1 or len(name.strip()) > 50:
        return False, "Имя должно быть от 1 до 50 символов."
    safe_name = name.strip()
    dangerous = ["--", ";", "/*", "*/", "union", "select", "drop", "insert"]
    if any(d in safe_name.lower() for d in dangerous):
        return False, "Имя содержит недопустимые символы."
    return True, safe_name


@dp.message(Command(commands=["start"]))
async def process_start_command(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    if user_id in users:
        await message.answer(f"Привет, {users[user_id]['name']}! Ты уже зарегистрирован. Используй /exp4, /exp8, /exp12 или /exp20 для установки экспедиции.")
        return
    await message.answer('Привет! Введи своё имя (для Genshin-уведомлений):')
    await state.set_state(UserStates.waiting_name)


@dp.message(UserStates.waiting_name)
async def process_name(message: Message, state: FSMContext):
    is_valid, result = validate_name(message.text)
    if not is_valid:
        await message.answer(f"Ошибка: {result}\nПопробуй снова.")
        return
    await state.update_data(name=result)
    await message.answer('Отлично! Теперь введи свой часовой пояс (offset от UTC в часах, например, 3 для +3 или -5 для -5):')
    await state.set_state(UserStates.waiting_timezone)


@dp.message(UserStates.waiting_timezone)
async def process_timezone(message: Message, state: FSMContext):
    try:
        tz_offset = int(message.text)
        if abs(tz_offset) > 12:
            raise ValueError("Offset слишком большой.")
    except ValueError as e:
        await message.answer(f"Ошибка: введи целое число (e.g., 3). {e}")
        return

    data = await state.get_data()
    name = data['name']
    user_id = str(message.from_user.id)
    users[user_id] = {
        'name': name,
        'timezone': tz_offset,
        'expedition_end': None  # UTC timestamp
    }
    save_users(users)
    await state.clear()
    await message.answer(f"Регистрация завершена! Привет, {name}. Твой пояс: UTC{tz_offset:+d}.\n"
                         f"Используй /exp4, /exp8, /exp12 или /exp20 для установки времени экспедиции.")


@dp.message(Command(commands=['help']))
async def process_help_command(message: Message):
    help_text = """
Гenshin Expedition Bot:
/start — регистрация (имя + пояс)
/exp4 — экспедиция на 4 часа
/exp8 — на 8 часов
/exp12 — на 12 часов
/exp20 — на 20 часов
/expstatus — текущий статус экспедиции
Когда время истечёт, бот напомнит!
    """
    await message.answer(help_text)

# Обработчики экспедиций


async def set_expedition(message: Message, hours: int):
    user_id = str(message.from_user.id)
    if user_id not in users:
        await message.answer("Сначала зарегистрируйся с /start!")
        return

    end_utc = datetime.now(timezone.utc) + timedelta(hours=hours)
    users[user_id]['expedition_end'] = end_utc.timestamp()
    save_users(users)

    user_tz = users[user_id]['timezone']
    end_local = end_utc + timedelta(hours=user_tz)
    await message.answer(f"Экспедиция установлена на {hours} часов!\n"
                         f"Завершится: {end_local.strftime('%Y-%m-%d %H:%M')} (по твоему времени).\n"
                         f"Уведомление прилетит автоматически.")


@dp.message(Command(commands=["exp4"]))
async def exp4_handler(message: Message):
    await set_expedition(message, 4)


@dp.message(Command(commands=["exp8"]))
async def exp8_handler(message: Message):
    await set_expedition(message, 8)


@dp.message(Command(commands=["exp12"]))
async def exp12_handler(message: Message):
    await set_expedition(message, 12)


@dp.message(Command(commands=["exp20"]))
async def exp20_handler(message: Message):
    await set_expedition(message, 20)


@dp.message(Command(commands=["expstatus"]))
async def expstatus_handler(message: Message):
    user_id = str(message.from_user.id)
    if user_id not in users:
        await message.answer("Сначала зарегистрируйся с /start!")
        return

    end_ts = users[user_id].get('expedition_end')
    if not end_ts:
        await message.answer("Экспедиция не установлена. Используй /exp4 и т.д.")
        return

    now_utc = datetime.now(timezone.utc).timestamp()
    remaining = (end_ts - now_utc) / 3600  # hours
    if remaining <= 0:
        await message.answer("Экспедиция уже завершена! (Если не получил уведомление — проверь спам.)")
    else:
        end_utc = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        user_tz = users[user_id]['timezone']
        end_local = end_utc + timedelta(hours=user_tz)
        await message.answer(f"Экспедиция активна.\nОсталось: ~{remaining:.1f} часов.\nЗавершится: {end_local.strftime('%Y-%m-%d %H:%M')} (твоё время).")

# Фоновая задача для проверки экспедиций


async def check_expeditions():
    while True:
        try:
            now_utc = datetime.now(timezone.utc).timestamp()
            expired_users = []
            for user_id, data in users.items():
                end_ts = data.get('expedition_end')
                if end_ts and now_utc >= end_ts:
                    expired_users.append((user_id, data))

            for user_id, data in expired_users:
                # Получаем end_ts для каждого пользователя
                end_ts = data['expedition_end']
                name = data['name']
                user_tz = data['timezone']
                end_utc = datetime.fromtimestamp(end_ts, tz=timezone.utc)
                end_local = end_utc + timedelta(hours=user_tz)
                try:
                    await bot.send_message(int(user_id), f"🚨 Привет, {name}! Экспедиция в Genshin завершена.\nВремя по твоему поясу: {end_local.strftime('%H:%M %d.%m.%Y')}.")
                except Exception as send_e:
                    logging.error(
                        f"Failed to send notification to {user_id}: {send_e}")
                data['expedition_end'] = None  # Сброс

            if expired_users:
                save_users(users)
                logging.info(
                    f"Отправлено {len(expired_users)} уведомлений (некоторые могли не дойти).")

            await asyncio.sleep(60)  # Проверка каждую минуту
        except Exception as e:
            logging.error(f"Ошибка в check_expeditions: {e}")
            await asyncio.sleep(60)

# Эхо для нераспознанных сообщений (опционально)


@dp.message()
async def send_echo(message: Message):
    await message.reply("Не понял команду. Используй /help для справки.")

if __name__ == '__main__':
    async def main():
        # Запускаем фоновую задачу
        asyncio.create_task(check_expeditions())
        # Polling
        await dp.start_polling(bot)

    asyncio.run(main())
