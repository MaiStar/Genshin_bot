### -------------------------------------------------###

# pip install aiogram python-dotenv
# Для хранения данных пока используем JSON файл (users.json).
# Позже можно перейти на SQLite: простая БД для Telegram ботов.
# Пример схемы для SQLite:
# CREATE TABLE users (user_id INTEGER PRIMARY KEY, name TEXT, timezone INTEGER, expedition_end REAL, resin_set_time REAL, resin_at_set INTEGER, notified_192 BOOLEAN, notified_full BOOLEAN);  # end as unix timestamp
# Для уведомлений: используем asyncio task для периодической проверки (каждую минуту).
# Это не идеально для production (лучше APScheduler или Celery), но для старта ок.
# Часовой пояс: храним как offset от UTC в часах (e.g., 3 для +3).
# Проверка имени: длина 1-50, без SQL-опасных символов (--, ';', etc.).
# Время экспедиции: /exp4, /exp8, /exp12, /exp20 — устанавливает конец в UTC.
# Уведомление: "Привет, [name]! Экспедиция в Genshin завершена. Время по твоему поясу: [local_time]."
# Новый функционал: /resin <число> — устанавливает текущее значение смолы.
# Смола восстанавливается 1 за 8 минут, max=200.
# Отображает время до полного заполнения.
# Уведомления: при 192 — "Смола заполниться через час", при 200 — "Смола заполнена"
# Новое: Inline-кнопки для меню (/menu)

# @BotFather
# GenshinExpedition_bot
# BOT_TOKEN хранится в .env
# python3 -m venv myenv
# source myenv/bin/activate
# deactivate
# pip freeze > requirements.txt
# pip install -r requirements.txt
# pip install python-dotenv
# Добавь .env в .gitignore

### -------------------------------------------------###

import time
import asyncio
import logging
import json
import os
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Настраиваем логирование
logging.basicConfig(
    level=logging.DEBUG,  # Установите DEBUG для максимального логирования
    format="%(asctime)s - %(levelname)s - %(message)s",
)

BOT_TOKEN = os.getenv('BOT_TOKEN')  # Получаем токен из окружения
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле!")

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
    waiting_resin = State()  # Для ввода смолы через FSM (опционально для user-friendly)

# Проверка имени на безопасность (длина и базовая защита от инъекций)


def validate_name(name: str) -> tuple[bool, str]:
    if not name or len(name.strip()) < 1 or len(name.strip()) > 50:
        return False, "Имя должно быть от 1 до 50 символов."
    safe_name = name.strip()
    dangerous = ["--", ";", "/*", "*/", "union", "select", "drop", "insert"]
    if any(d in safe_name.lower() for d in dangerous):
        return False, "Имя содержит недопустимые символы."
    return True, safe_name

# Reply клавиатура


def get_reply_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="Menu")]
    ])
    return markup


@dp.message(Command(commands=["start"]))
async def process_start_command(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    if user_id in users:
        await message.answer(
            f"Привет, {users[user_id]['name']}! Ты уже зарегистрирован. Используй /menu для выбора действий.",
            reply_markup=get_reply_keyboard()
        )
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
    await message.answer('Отлично! Теперь введи свой часовой пояс (offset от UTC в часах, например, 3 или -5):')
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
        'expedition_end': None,  # UTC timestamp
        'resin_set_time': None,
        'resin_at_set': None,
        'notified_192': False,
        'notified_full': False
    }
    save_users(users)
    await state.clear()
    await message.answer(f"Регистрация завершена! Привет, {name}. Твой пояс: UTC{tz_offset:+d}.\n"
                         f"Используй /menu для выбора экспедиции или смолы.")


@dp.message(Command(commands=['help']))
async def process_help_command(message: Message):
    help_text = """
Genshin Bot:
/start — регистрация
/menu — меню с inline кнопками
/expstatus — статус экспедиции
/resinstatus — статус смолы
    """
    await message.answer(help_text, reply_markup=get_reply_keyboard())

# Меню с inline-кнопками


# Inline-меню
@dp.message(Command(commands=["menu"]))
@dp.message(F.text == "Menu")  # обработка кнопки Menu
async def show_menu(message: Message):
    user_id = str(message.from_user.id)
    if user_id not in users:
        await message.answer("Сначала зарегистрируйся с /start!")
        return

    inline_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Экспедиция 4 часа", callback_data="exp_4"),
         InlineKeyboardButton(text="Экспедиция 8 часов", callback_data="exp_8")],
        [InlineKeyboardButton(text="Экспедиция 12 часов", callback_data="exp_12"),
         InlineKeyboardButton(text="Экспедиция 20 часов", callback_data="exp_20")],
        [InlineKeyboardButton(text="Установить смолу", callback_data="set_resin"),
         InlineKeyboardButton(text="Статус", callback_data="status")]
    ])
    await message.answer("Выбери действие:", reply_markup=inline_markup)

# Обработчик callback от inline-кнопок


@dp.callback_query(F.data.startswith("exp_"))
async def handle_exp_callback(callback: CallbackQuery):
    hours = int(callback.data.split("_")[1])
    user_id = str(callback.from_user.id)
    if user_id not in users:
        await callback.answer("Сначала зарегистрируйся!")
        return

    end_utc = datetime.now(timezone.utc) + timedelta(hours=hours)
    users[user_id]['expedition_end'] = end_utc.timestamp()
    save_users(users)

    user_tz = users[user_id]['timezone']
    end_local = end_utc + timedelta(hours=user_tz)
    await callback.answer(f"Экспедиция на {hours} часов установлена!")
    await callback.message.edit_text(f"Экспедиция установлена на {hours} часов!\n"
                                     f"Завершится: {end_local.strftime('%Y-%m-%d %H:%M')} (по твоему времени).")


@dp.callback_query(F.data == "set_resin")
async def handle_set_resin_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Введи текущее значение смолы (0-200):")
    await state.set_state(UserStates.waiting_resin)
    await callback.message.edit_text("Введи смолу:")


@dp.message(UserStates.waiting_resin)
async def process_resin(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    try:
        current_resin = int(message.text)
        if current_resin < 0 or current_resin > 200:
            raise ValueError("Значение должно быть от 0 до 200.")
    except ValueError as e:
        await message.answer(f"Ошибка: {e}. Попробуй снова.")
        return

    now_utc = datetime.now(timezone.utc)
    users[user_id]['resin_set_time'] = now_utc.timestamp()
    users[user_id]['resin_at_set'] = current_resin
    users[user_id]['notified_192'] = False
    users[user_id]['notified_full'] = False
    save_users(users)

    max_resin = 200
    remaining_resin = max_resin - current_resin
    time_to_full_minutes = remaining_resin * 8
    full_time_utc = now_utc + timedelta(minutes=time_to_full_minutes)
    user_tz = users[user_id]['timezone']
    full_time_local = full_time_utc + timedelta(hours=user_tz)

    hours_to_full = time_to_full_minutes // 60
    minutes_to_full = time_to_full_minutes % 60

    await message.answer(f"Смола установлена на {current_resin}.\n"
                         f"Полное заполнение: {full_time_local.strftime('%H:%M %d.%m.%Y')} (твоё время).\n"
                         f"Через: {hours_to_full} часов {minutes_to_full} минут.")
    await state.clear()


@dp.callback_query(F.data == "status")
async def handle_status_callback(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    # Здесь можно вывести статус экспедиции и смолы (комбинировать expstatus и resinstatus)
    status_text = "Статус:\n"
    # Добавьте логику из expstatus и resinstatus
    end_ts = users[user_id].get('expedition_end')
    if end_ts:
        now_utc = datetime.now(timezone.utc).timestamp()
        remaining = (end_ts - now_utc) / 3600
        if remaining > 0:
            status_text += f"Экспедиция: ~{remaining:.1f} часов осталось.\n"
        else:
            status_text += "Экспедиция завершена.\n"
    else:
        status_text += "Экспедиция не установлена.\n"

    current_resin = await get_current_resin(user_id)
    status_text += f"Смола: {current_resin}/200."

    await callback.answer("Статус показан!")
    await callback.message.edit_text(status_text)

# Опционально: Reply-клавиатура (постоянная)
# def get_reply_keyboard():
#     markup = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
#         [KeyboardButton(text="Экспедиция 4ч"), KeyboardButton(text="Экспедиция 8ч")],
#         [KeyboardButton(text="Смола"), KeyboardButton(text="Статус")]
#     ])
#     return markup
#
# Затем в /start: await message.answer(..., reply_markup=get_reply_keyboard())
# И обработчики @dp.message(F.text == "Экспедиция 4ч") и т.д.

# Обработчики экспедиций (оставлены для совместимости)


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

# Новый функционал: установка смолы (оставлен для совместимости)


@dp.message(Command(commands=["resin"]))
async def set_resin_handler(message: Message):
    user_id = str(message.from_user.id)
    if user_id not in users:
        await message.answer("Сначала зарегистрируйся с /start!")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /resin <число> (текущее значение смолы, 0-200)")
        return

    try:
        current_resin = int(parts[1])
        if current_resin < 0 or current_resin > 200:
            raise ValueError("Значение должно быть от 0 до 200.")
    except ValueError as e:
        await message.answer(f"Ошибка: {e}")
        return

    now_utc = datetime.now(timezone.utc)
    users[user_id]['resin_set_time'] = now_utc.timestamp()
    users[user_id]['resin_at_set'] = current_resin
    users[user_id]['notified_192'] = False
    users[user_id]['notified_full'] = False
    save_users(users)

    max_resin = 200
    remaining_resin = max_resin - current_resin
    time_to_full_minutes = remaining_resin * 8
    full_time_utc = now_utc + timedelta(minutes=time_to_full_minutes)
    user_tz = users[user_id]['timezone']
    full_time_local = full_time_utc + timedelta(hours=user_tz)

    hours_to_full = time_to_full_minutes // 60
    minutes_to_full = time_to_full_minutes % 60

    await message.answer(f"Смола установлена на {current_resin}.\n"
                         f"Полное заполнение: {full_time_local.strftime('%H:%M %d.%m.%Y')} (твоё время).\n"
                         f"Через: {hours_to_full} часов {minutes_to_full} минут.\n"
                         f"Уведомления придут автоматически.")

# Статус смолы


@dp.message(Command(commands=["resinstatus"]))
async def resinstatus_handler(message: Message):
    user_id = str(message.from_user.id)
    if user_id not in users:
        await message.answer("Сначала зарегистрируйся с /start!")
        return

    if not users[user_id].get('resin_set_time'):
        await message.answer("Смола не установлена. Используй /resin <число>.")
        return

    current = await get_current_resin(user_id)
    max_resin = 200
    if current >= max_resin:
        await message.answer(f"Смола: {current}/{max_resin} (полная).")
        return

    remaining_resin = max_resin - current
    time_to_full_minutes = remaining_resin * 8
    now_utc = datetime.now(timezone.utc)
    full_time_utc = now_utc + timedelta(minutes=time_to_full_minutes)
    user_tz = users[user_id]['timezone']
    full_time_local = full_time_utc + timedelta(hours=user_tz)

    hours_to_full = time_to_full_minutes // 60
    minutes_to_full = time_to_full_minutes % 60

    await message.answer(f"Текущая смола: {current}/{max_resin}.\n"
                         f"Полное заполнение: {full_time_local.strftime('%H:%M %d.%m.%Y')} (твоё время).\n"
                         f"Через: {hours_to_full} часов {minutes_to_full} минут.")


async def get_current_resin(user_id: str) -> int:
    data = users[user_id]
    if not data.get('resin_set_time'):
        return 0

    now_utc = datetime.now(timezone.utc).timestamp()
    elapsed_seconds = now_utc - data['resin_set_time']
    recovered = elapsed_seconds // (8 * 60)
    current = data['resin_at_set'] + int(recovered)
    max_resin = 200
    return min(current, max_resin)

# Фоновая задача для проверки


async def check_tasks():
    while True:
        try:
            now_utc_ts = datetime.now(timezone.utc).timestamp()
            expired_expeditions = []
            for user_id, data in users.items():
                # Проверка экспедиций
                end_ts = data.get('expedition_end')
                if end_ts and now_utc_ts >= end_ts:
                    expired_expeditions.append((user_id, data))

                # Проверка смолы
                current_resin = await get_current_resin(user_id)
                if current_resin >= 192 and not data.get('notified_192', False):
                    try:
                        await bot.send_message(int(user_id), "🚨 Смола достигла 192! Заполнится через ~час.")
                        data['notified_192'] = True
                    except Exception as send_e:
                        logging.error(
                            f"Failed to send 192 notification to {user_id}: {send_e}")

                if current_resin >= 200 and not data.get('notified_full', False):
                    try:
                        await bot.send_message(int(user_id), "🚨 Смола заполнена (200)!")
                        data['notified_full'] = True
                    except Exception as send_e:
                        logging.error(
                            f"Failed to send full notification to {user_id}: {send_e}")

            # Обработка экспедиций
            for user_id, data in expired_expeditions:
                end_ts = data['expedition_end']
                name = data['name']
                user_tz = data['timezone']
                end_utc = datetime.fromtimestamp(end_ts, tz=timezone.utc)
                end_local = end_utc + timedelta(hours=user_tz)
                try:
                    await bot.send_message(int(user_id), f"🚨 Привет, {name}! Экспедиция в Genshin завершена.\nВремя по твоему поясу: {end_local.strftime('%H:%M %d.%m.%Y')}.")
                except Exception as send_e:
                    logging.error(
                        f"Failed to send expedition notification to {user_id}: {send_e}")
                data['expedition_end'] = None  # Сброс

            if expired_expeditions or any(data.get('notified_192') or data.get('notified_full') for data in users.values()):
                save_users(users)
                logging.info(f"Отправлены уведомления.")

            await asyncio.sleep(60)  # Проверка каждую минуту
        except Exception as e:
            logging.error(f"Ошибка в check_tasks: {e}")
            await asyncio.sleep(60)

# Эхо для нераспознанных сообщений


@dp.message()
async def send_echo(message: Message):
    await message.reply("Не понял команду. Используй /menu или /help для справки.", reply_markup=get_reply_keyboard())


if __name__ == '__main__':
    async def main():
        # Запускаем фоновую задачу
        asyncio.create_task(check_tasks())
        # Polling
        await dp.start_polling(bot)

    asyncio.run(main())
