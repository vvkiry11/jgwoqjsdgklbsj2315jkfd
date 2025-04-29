import os
import sqlite3
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from urllib.parse import quote
import html

# Конфигурация
BOT_TOKEN = ""
ADMIN_IDS = [7039967412]  # ID администраторов
WATA_ACCESS_TOKEN = ""
WATA_API_URL = "https://api.wata.pro/api/h2h/links"
WATA_PAYMENT_URL = "https://payment.wata.pro/pay-form"


logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()



# Подключение к базе данных
conn = sqlite3.connect('data//shop.db')
cursor = conn.cursor()



# Создание таблиц
def create_tables():
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round_number INTEGER NOT NULL,
        login TEXT NOT NULL,
        password TEXT NOT NULL,
        sold INTEGER DEFAULT 0
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users_promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        promo_code TEXT NOT NULL,
        used_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, promo_code)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payment_id TEXT UNIQUE NOT NULL,
        order_id TEXT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS purchased_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    login TEXT NOT NULL,
    password TEXT NOT NULL,
    purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS purchased_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        round_number INTEGER NOT NULL,
        purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (account_id) REFERENCES accounts (id)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS round_prices (
        round_number INTEGER PRIMARY KEY,
        price REAL NOT NULL
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY,
        amount REAL NOT NULL,
        max_uses INTEGER NOT NULL,
        used_count INTEGER DEFAULT 0,
        expiration_date TIMESTAMP
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS round_images (
        round_number INTEGER PRIMARY KEY,
        image_path TEXT
    )
    ''')
    
    conn.commit()

create_tables()

# Модели состояний
class AddAccounts(StatesGroup):
    round_number = State()
    accounts_text = State()

class DatabaseStates(StatesGroup):
    waiting_for_backup = State()

class AddPromo(StatesGroup):
    code = State()
    amount = State()
    max_uses = State()
    duration_days = State()

# Добавьте это перед обработчиками
class PaymentStates(StatesGroup):
    waiting_for_amount = State()

class TopUpStates(StatesGroup):
    waiting_for_amount = State()

class DatabaseStates(StatesGroup):
    waiting_for_backup = State()

class SetPrice(StatesGroup):
    round_number = State()
    price = State()

class SetImage(StatesGroup):
    round_number = State()
    image = State()

class UsePromo(StatesGroup):
    code = State()

# Добавляем новое состояние для пополнения баланса
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()




# Главное меню
async def main_menu(user_id: int = None):
    builder = InlineKeyboardBuilder()
    builder.button(text="🎮 Доступные раунды", callback_data="show_rounds")
    builder.button(text="💰 Профиль", callback_data="profile")
    builder.button(text="🎁 Активировать промокод", callback_data="use_promo")
    builder.button(text="💰 Пополнить баланс", callback_data="topup")
    
    if user_id and user_id in ADMIN_IDS:
        builder.button(text="🔧 Админ-панель", callback_data="admin_panel")
    
    builder.adjust(1)
    return builder.as_markup()

# Админ-панель
def admin_panel():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить аккаунты", callback_data="add_accounts")
    builder.button(text="🎫 Создать промокод", callback_data="add_promo")
    builder.button(text="💵 Установить цену", callback_data="set_price")
    builder.button(text="🖼 Установить изображение", callback_data="set_image")
    builder.button(text="💰 Пополнить баланс", callback_data="admin_topup")  # Новая кнопка
    builder.button(text="💾 Выгрузить БД", callback_data="export_db")
    builder.button(text="🔄 Загрузить БД", callback_data="restore_db")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(2)
    return builder.as_markup()

# Хэндлер команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🛒 Добро пожаловать в магазин Twitch Drops!\n"
        "Выберите действие:",
        reply_markup=await main_menu(message.from_user.id)
    )


# Обработчики callback-запросов
@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Главное меню:",
        reply_markup=await main_menu(callback.from_user.id)
    )
    await callback.answer()

@dp.callback_query(F.data == "show_rounds")
async def show_rounds_handler(callback: types.CallbackQuery):
    await show_rounds(callback.message, edit=True)
    await callback.answer()

@dp.message(Command("restore_db"))
async def restore_db_command(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("❌ Доступ запрещен")
    
    await message.answer(
        "⚠️ Отправьте мне файл базы данных (shop.db) для восстановления.\n"
        "Текущая БД будет заменена! Создайте бэкап перед этим."
    )
    await state.set_state(DatabaseStates.waiting_for_backup)



@dp.callback_query(F.data == "custom_amount")
async def custom_amount_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите сумму для пополнения (минимум 25 RUB):")
    await state.set_state(PaymentStates.waiting_for_amount)
    await callback.answer()

@dp.message(PaymentStates.waiting_for_amount)
async def process_custom_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        if amount < 25:
            await message.answer("❌ Минимальная сумма пополнения - 25 RUB")
            return
        
        user_id = message.from_user.id
        message_text = (
            f"Пополнение баланса в боте Rust Tools\n\n"
            f"ID пользователя: {user_id}\n"
            f"Сумма: {amount} RUB"
        )
        
        # Формируем текст сообщения
        message_text = f"Сумма платежа: {amount}\nTelegram id: `{user_id}`"
        
        # Правильно кодируем текст для URL
        encoded_text = quote(message_text)
        
        # Формируем deep link
        deeplink = f"https://t.me/RustTools_help?text={encoded_text}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"💳 Перевести {amount} RUB",
                url=deeplink
            )
        ]])
        
        await message.answer(
            f"🔹 Для пополнения баланса на {amount} RUB:\n\n"
            f"1. Нажмите кнопку 'Перевести {amount} RUB'\n"
            f"2. В открывшемся чате нажмите 'Отправить'\n"
            f"3. Совершите перевод {amount} RUB\n"
            f"4. Нажмите 'Проверить оплату'\n\n"
            f"Ваш ID: <code>{user_id}</code>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Введите корректную сумму (число):")



@dp.message(DatabaseStates.waiting_for_backup, F.document)
async def process_db_restore(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("❌ Доступ запрещен")
    
    if not message.document.file_name or not message.document.file_name.endswith('.db'):
        return await message.answer("❌ Файл должен иметь расширение .db")
    
    try:
        # Скачиваем временный файл
        temp_path = f"temp_restore_{message.document.file_name}"
        await bot.download(message.document, destination=temp_path)
        
        # Вызываем функцию восстановления
        success = await restore_database(temp_path, message.from_user.id)
        
        if success:
            await message.answer("✅ База данных успешно восстановлена!")
        else:
            await message.answer("❌ Не удалось восстановить БД")
            
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        await state.clear()
        if os.path.exists(temp_path):
            os.remove(temp_path)



@dp.callback_query(F.data == "profile")
async def profile_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cursor.fetchone()
    balance = balance[0] if balance else 0
    
    cursor.execute('''
    SELECT round_number, login, password, purchase_date 
    FROM purchased_accounts 
    WHERE user_id = ?
    ORDER BY purchase_date DESC
    ''', (user_id,))
    accounts = cursor.fetchall()
    
    text = f"💰 Ваш баланс: {balance} RUB\n\n"
    text += "🛒 Купленные аккаунты:\n"
    
    if accounts:
        for acc in accounts:
            text += f"\nРаунд {acc[0]}: {acc[1]}:{acc[2]}\nДата покупки: {acc[3]}\n"
    else:
        text += "\nУ вас пока нет купленных аккаунтов."
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="main_menu")
    
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == "use_promo")
async def use_promo_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введите промокод:")
    await state.set_state(UsePromo.code)
    await callback.answer()

@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "Админ-панель:",
        reply_markup=admin_panel()
    )
    await callback.answer()

@dp.callback_query(F.data == "add_accounts")
async def add_accounts_handler(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text("Введите номер раунда:")
    await state.set_state(AddAccounts.round_number)
    await callback.answer()

@dp.callback_query(F.data == "add_promo")
async def add_promo_handler(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text("Введите промокод:")
    await state.set_state(AddPromo.code)
    await callback.answer()

@dp.callback_query(F.data == "set_price")
async def set_price_handler(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text("Введите номер раунда:")
    await state.set_state(SetPrice.round_number)
    await callback.answer()

@dp.callback_query(F.data == "set_image")
async def set_image_handler(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text("Введите номер раунда:")
    await state.set_state(SetImage.round_number)
    await callback.answer()

# Обработчик кнопки пополнения баланса в админ-панели
@dp.callback_query(F.data == "admin_topup")
async def admin_topup_handler(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "Введите ID пользователя, которому нужно пополнить баланс:",
    )
    await state.set_state(AdminStates.waiting_for_user_id)
    await callback.answer()

# Обработчик ввода ID пользователя
@dp.message(AdminStates.waiting_for_user_id)
async def process_user_id(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await state.update_data(user_id=user_id)
        await message.answer("Введите сумму для пополнения:")
        await state.set_state(AdminStates.waiting_for_amount)
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом. Попробуйте еще раз.")

# Обработчик ввода суммы и пополнение баланса
@dp.message(AdminStates.waiting_for_amount)
async def process_admin_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0.")
            return
        
        data = await state.get_data()
        user_id = data['user_id']
        
        # Пополняем баланс
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)",
            (user_id,)
        )
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        conn.commit()
        
        # Уведомляем админа
        await message.answer(
            f"✅ Баланс пользователя {user_id} пополнен на {amount} RUB"
        )
        
        # Уведомляем пользователя (если возможно)
        try:
            await bot.send_message(
                user_id,
                f"💰 Администратор пополнил ваш баланс на {amount} RUB\n"
                f"💳 Новый баланс: {get_user_balance(user_id)} RUB"
            )
        except:
            pass
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Введите корректную сумму (число).")

# Вспомогательная функция для получения баланса
def get_user_balance(user_id: int) -> float:
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cursor.fetchone()
    return balance[0] if balance else 0

# Обновляем обработчик админ-панели
@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "Админ-панель:",
        reply_markup=admin_panel()
    )
    await callback.answer()


# Хэндлеры состояний
@dp.message(AddAccounts.round_number)
async def process_round_number(message: types.Message, state: FSMContext):
    try:
        round_number = int(message.text)
        await state.update_data(round_number=round_number)
        await message.answer("Введите аккаунты в формате login:password, каждый с новой строки:")
        await state.set_state(AddAccounts.accounts_text)
    except ValueError:
        await message.answer("Номер раунда должен быть числом. Попробуйте еще раз.")

@dp.message(AddAccounts.accounts_text)
async def process_accounts_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    round_number = data['round_number']
    accounts = message.text.split('\n')
    
    added = 0
    for acc in accounts:
        if ':' in acc:
            login, password = acc.split(':', 1)
            cursor.execute(
                "INSERT INTO accounts (round_number, login, password) VALUES (?, ?, ?)",
                (round_number, login.strip(), password.strip())
            )
            added += 1
    
    conn.commit()
    await message.answer(f"Добавлено {added} аккаунтов для раунда {round_number}.")
    await state.clear()

@dp.message(AddPromo.code)
async def process_promo_code(message: types.Message, state: FSMContext):
    code = message.text.upper()
    cursor.execute("SELECT code FROM promo_codes WHERE code = ?", (code,))
    if cursor.fetchone():
        await message.answer("Этот промокод уже существует. Введите другой:")
        return
    
    await state.update_data(code=code)
    await message.answer("Введите сумму бонуса:")
    await state.set_state(AddPromo.amount)

@dp.message(AddPromo.duration_days)
async def process_promo_duration(message: types.Message, state: FSMContext):
    try:
        duration_days = int(message.text)
        if duration_days <= 0:
            await message.answer("Срок действия должен быть больше 0 дней. Введите число:")
            return
        
        expiration_date = (datetime.now() + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")
        await state.update_data(expiration_date=expiration_date)
        
        data = await state.get_data()
        cursor.execute(
            "INSERT INTO promo_codes (code, amount, max_uses, expiration_date) VALUES (?, ?, ?, ?)",
            (data['code'], data['amount'], data['max_uses'], data['expiration_date'])
        )
        conn.commit()
        
        await message.answer(
            f"🎟 Промокод создан!\n\n"
            f"🔑 Код: {data['code']}\n"
            f"💰 Сумма: {data['amount']} RUB\n"
            f"🔄 Макс. использований: {data['max_uses']}\n"
            f"⏳ Действует {duration_days} дней (до: {data['expiration_date']})"
        )
        await state.clear()
    except ValueError:
        await message.answer("Введите корректное число дней:")
        return

@dp.callback_query(F.data == "check_balance")
async def check_balance(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cursor.fetchone()
    balance = balance[0] if balance else 0
    await callback.answer(f"💰 Ваш баланс: {balance} RUB", show_alert=True)

@dp.message(AddPromo.amount)
async def process_promo_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        await state.update_data(amount=amount)
        await message.answer("Введите максимальное количество использований:")
        await state.set_state(AddPromo.max_uses)
    except ValueError:
        await message.answer("Сумма должна быть числом. Попробуйте еще раз:")

@dp.message(AddPromo.max_uses)
async def process_promo_max_uses(message: types.Message, state: FSMContext):
    try:
        max_uses = int(message.text)
        if max_uses <= 0:
            await message.answer("Количество использований должно быть больше 0. Введите число:")
            return
        
        await state.update_data(max_uses=max_uses)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="1 день", callback_data="duration_1")
        builder.button(text="7 дней", callback_data="duration_7")
        builder.button(text="30 дней", callback_data="duration_30")
        builder.button(text="Другой срок", callback_data="duration_custom")
        builder.adjust(2)
        
        await message.answer(
            "Выберите срок действия промокода:",
            reply_markup=builder.as_markup()
        )
    except ValueError:
        await message.answer("Введите корректное число:")

@dp.callback_query(F.data == "topup")
async def start_topup(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введите сумму пополнения в рублях (от 1):")
    await state.set_state(TopUpStates.waiting_for_amount)
    await callback.answer()

@dp.message(TopUpStates.waiting_for_amount)
async def process_topup_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        if amount < 1 or amount > 1000000:
            await message.answer("❌ Сумма должна быть от 1 RUB. Попробуйте снова.")
            return

        payment_url, payment_id, order_id = await create_wata_payment(message.from_user.id, amount)

        if not payment_url:
            await message.answer("❌ Не удалось создать платёж. Попробуйте позже.")
            await state.clear()
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"checkpay_{payment_id}")]
        ])

        await message.answer(
            f"💵 Сумма к оплате: {amount:.2f} RUB\n\n"
            f"🔗 Paymentlink ID: <code>{payment_id}</code>\n\n"
            f"После оплаты нажмите «🔄 Проверить оплату».",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except ValueError:
        await message.answer("❌ Введите число.")
    finally:
        await state.clear()

async def create_wata_payment(user_id: int, amount: float):
    order_id = f"{user_id}_{int(datetime.now().timestamp())}"
    headers = {
        "Authorization": f"Bearer {WATA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "amount": amount,
        "currency": "RUB",
        "description": f"Пополнение баланса для {user_id}",
        "orderId": order_id,
        "successRedirectUrl": "https://t.me/RustTools_bot",
        "failRedirectUrl": "https://t.me/RustTools_bot"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(WATA_API_URL, headers=headers, json=payload) as resp:
            data = await resp.json()
            if "id" in data and "url" in data:
                cursor.execute("""
                    INSERT INTO payments (payment_id, user_id, amount, status, order_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (data["id"], user_id, amount, "pending", order_id))
                conn.commit()
                return data["url"], data["id"], order_id
            return None, None, None

@dp.callback_query(F.data.startswith("checkpay_"))
async def check_payment(callback: types.CallbackQuery):
    payment_id = callback.data.split("_")[1]
    headers = {"Authorization": f"Bearer {WATA_ACCESS_TOKEN}"}

    # Получаем данные из БД
    cursor.execute("SELECT user_id, amount, status FROM payments WHERE payment_id = ?", (payment_id,))
    row = cursor.fetchone()
    if not row:
        await callback.message.edit_text("❌ Платёж не найден в базе.")
        return

    user_id, amount, old_status = row

    # Проверяем статус ссылки
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.wata.pro/api/h2h/links/{payment_id}", headers=headers) as resp:
            if resp.status != 200:
                await callback.answer(f"❌ Ошибка проверки платежа ({resp.status})", show_alert=True)
                return
            data = await resp.json()

    status = data.get("status", "")
    if status in ["Paid", "Closed"]:
        if old_status != "paid":
            cursor.execute("UPDATE payments SET status = 'paid' WHERE payment_id = ?", (payment_id,))
            cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
            conn.commit()
        await callback.message.edit_text(f"✅ Оплата прошла успешно! Баланс пополнен на {amount} RUB.")

    elif status == "Opened":
        await callback.answer("⏳ Платёж ещё не завершён.", show_alert=True)
    elif status == "Expired":
        await callback.message.edit_text("❌ Платёж истёк. Попробуйте снова.")
    else:
        await callback.message.edit_text(f"❗ Статус платежа: {status}")



# Обработчики кнопок выбора срока
@dp.callback_query(F.data.startswith("duration_"))
async def process_duration_choice(callback: types.CallbackQuery, state: FSMContext):
    duration = callback.data.split("_")[1]
    
    if duration == "custom":
        await callback.message.answer("Введите количество дней действия промокода:")
        await state.set_state(AddPromo.duration_days)
    else:
        try:
            duration_days = int(duration)
            expiration_date = (datetime.now() + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")
            await state.update_data(expiration_date=expiration_date)
            
            data = await state.get_data()
            cursor.execute(
                "INSERT INTO promo_codes (code, amount, max_uses, expiration_date) VALUES (?, ?, ?, ?)",
                (data['code'], data['amount'], data['max_uses'], data['expiration_date'])
            )
            conn.commit()
            
            await callback.message.answer(
                f"🎟 Промокод создан!\n\n"
                f"🔑 Код: {data['code']}\n"
                f"💰 Сумма: {data['amount']} RUB\n"
                f"🔄 Макс. использований: {data['max_uses']}\n"
                f"⏳ Действует {duration_days} дней (до: {data['expiration_date']})"
            )
            await state.clear()
        except ValueError:
            await callback.answer("Ошибка при создании промокода", show_alert=True)
    
    await callback.answer()
@dp.message(SetPrice.round_number)
async def process_set_price_round(message: types.Message, state: FSMContext):
    try:
        round_number = int(message.text)
        await state.update_data(round_number=round_number)
        await message.answer("Введите новую цену для этого раунда:")
        await state.set_state(SetPrice.price)
    except ValueError:
        await message.answer("Номер раунда должен быть числом. Попробуйте еще раз.")

@dp.message(SetPrice.price)
async def process_set_price_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text)
        data = await state.get_data()
        round_number = data['round_number']
        
        cursor.execute(
            "INSERT OR REPLACE INTO round_prices (round_number, price) VALUES (?, ?)",
            (round_number, price)
        )
        conn.commit()
        
        await message.answer(f"Цена для раунда {round_number} установлена: {price} RUB")
        await state.clear()
    except ValueError:
        await message.answer("Цена должна быть числом. Попробуйте еще раз.")

@dp.message(SetImage.round_number)
async def process_set_image_round(message: types.Message, state: FSMContext):
    try:
        round_number = int(message.text)
        await state.update_data(round_number=round_number)
        await message.answer("Отправьте изображение для этого раунда:")
        await state.set_state(SetImage.image)
    except ValueError:
        await message.answer("Номер раунда должен быть числом. Попробуйте еще раз.")

@dp.message(SetImage.image, F.photo)
async def process_set_image_image(message: types.Message, state: FSMContext):
    data = await state.get_data()
    round_number = data['round_number']
    
    # Создаем папку для изображений, если ее нет
    if not os.path.exists('images'):
        os.makedirs('images')
    
    # Сохраняем изображение
    photo = message.photo[-1]
    file_id = photo.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    
    # Скачиваем файл
    destination = f"images/round_{round_number}.jpg"
    await bot.download_file(file_path, destination)
    
    # Сохраняем путь в БД
    cursor.execute(
        "INSERT OR REPLACE INTO round_images (round_number, image_path) VALUES (?, ?)",
        (round_number, destination)
    )
    conn.commit()
    
    await message.answer(f"Изображение для раунда {round_number} сохранено.")
    await state.clear()

@dp.message(UsePromo.code)
async def process_use_promo(message: types.Message, state: FSMContext):
    code = message.text.upper()
    user_id = message.from_user.id
    
    # Проверяем промокод
    cursor.execute(
        "SELECT amount, max_uses, used_count, expiration_date FROM promo_codes WHERE code = ?",
        (code,)
    )
    promo_data = cursor.fetchone()
    
    if not promo_data:
        await message.answer("❌ Промокод не найден.")
        await state.clear()
        return
    
    amount, max_uses, used_count, expiration_date = promo_data
    
    try:
        # Преобразуем строку в datetime
        expiration_datetime = datetime.strptime(expiration_date.split('.')[0], "%Y-%m-%d %H:%M:%S")
        
        if datetime.now() > expiration_datetime:
            await message.answer("❌ Срок действия промокода истек.")
            await state.clear()
            return
    except Exception as e:
        print(f"Ошибка обработки даты: {e}")
        await message.answer("⚠ Ошибка обработки промокода. Попробуйте позже.")
        await state.clear()
        return
    
    if used_count >= max_uses:
        await message.answer("❌ Лимит использований промокода исчерпан.")
        await state.clear()
        return
    
    # Проверяем, использовал ли пользователь уже этот промокод
    cursor.execute(
        "SELECT 1 FROM users_promo_codes WHERE user_id = ? AND promo_code = ?",
        (user_id, code)
    )
    if cursor.fetchone():
        await message.answer("❌ Вы уже использовали этот промокод.")
        await state.clear()
        return
    
    # Добавляем запись об использовании
    cursor.execute(
        "INSERT INTO users_promo_codes (user_id, promo_code) VALUES (?, ?)",
        (user_id, code)
    )
    
    # Начисляем бонус
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)",
        (user_id,)
    )
    cursor.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id = ?",
        (amount, user_id)
    )
    
    # Увеличиваем счетчик использований
    cursor.execute(
        "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
        (code,)
    )
    
    conn.commit()
    
    await message.answer(f"✅ Промокод активирован! Вам начислено {amount} RUB.")
    await state.clear()

    

# Хэндлеры кнопок
@dp.callback_query(F.data.startswith("round_"))
async def show_round_accounts(callback: types.CallbackQuery):
    round_number = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    # Получаем цену раунда
    cursor.execute("SELECT price FROM round_prices WHERE round_number = ?", (round_number,))
    price_data = cursor.fetchone()
    
    if not price_data:
        await callback.answer("Цена для этого раунда не установлена.", show_alert=True)
        return
    
    price = price_data[0]
    
    # Получаем баланс пользователя
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance_data = cursor.fetchone()
    balance = balance_data[0] if balance_data else 0
    
    # Получаем изображение раунда
    cursor.execute("SELECT image_path FROM round_images WHERE round_number = ?", (round_number,))
    image_data = cursor.fetchone()
    
    # Формируем сообщение
    text = f"🛒 Раунд {round_number}\n"
    text += f"💰 Цена: {price} RUB\n"
    text += f"💳 Ваш баланс: {balance} RUB\n\n"
    
    if balance >= price:
        text += "Нажмите кнопку ниже, чтобы купить аккаунт."
    else:
        text += "У вас недостаточно средств. Пополните баланс."
    
    # Создаем клавиатуру
    builder = InlineKeyboardBuilder()
    
    if balance >= price:
        builder.button(text="Купить", callback_data=f"buy_{round_number}")
    
    builder.button(text="Назад", callback_data="back_to_rounds")
    builder.adjust(1)
    
    # Отправляем сообщение
    if image_data:
        photo = FSInputFile(image_data[0])
        await callback.message.answer_photo(
            photo,
            caption=text,
            reply_markup=builder.as_markup()
        )
    else:
        await callback.message.answer(
            text,
            reply_markup=builder.as_markup()
        )
    
    await callback.answer()

@dp.callback_query(F.data == "back_to_rounds")
async def back_to_rounds(callback: types.CallbackQuery):
    await show_rounds(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def buy_account(callback: types.CallbackQuery):
    round_number = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    cursor.execute("SELECT price FROM round_prices WHERE round_number = ?", (round_number,))
    price = cursor.fetchone()[0]
    
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cursor.fetchone()
    balance = balance[0] if balance else 0
    
    if balance >= price:
        cursor.execute(
            "SELECT login, password FROM accounts WHERE round_number = ? LIMIT 1",
            (round_number,)
        )
        account = cursor.fetchone()
        
        if account:
            login, password = account
            
            # Удаляем аккаунт из доступных
            cursor.execute(
                "DELETE FROM accounts WHERE round_number = ? AND login = ? AND password = ?",
                (round_number, login, password)
            )
            
            # Списываем средства
            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                (price, user_id)
            )
            
            # Добавляем в купленные
            cursor.execute(
                "INSERT INTO purchased_accounts (user_id, round_number, login, password) VALUES (?, ?, ?, ?)",
                (user_id, round_number, login, password)
            )
            
            conn.commit()
            
            await callback.message.answer(
                f"✅ <b>Покупка успешна!</b>\n\n"
                f"<b>Раунд:</b> {round_number}\n"
                f"<b>Логин:</b> <code>{login}</code>\n"
                f"<b>Пароль:</b> <code>{password}</code>\n\n"
                f"📌 <i>Нажмите на логин/пароль для копирования</i>\n\n"
                f"📚 Инструкция: https://clck.ru/3LNTzq\n"
                f"💬 Отзыв: @rusttools_reviews\n"
                f"🛠 Поддержка: @RustTools_help",
            parse_mode="HTML"
            )
            
            await callback.answer()
        else:
            await callback.answer("Аккаунты закончились.", show_alert=True)
    else:
        shortage = price - balance
        await callback.message.answer(
            f"❌ Недостаточно средств на балансе!\n"
            f"💵 Товар стоит: {price} RUB\n"
            f"💰 Ваш баланс: {balance} RUB\n"
            f"🔻 Не хватает: {shortage} RUB\n\n"
            f"Хотите пополнить баланс?",
            reply_markup=await payment_options(user_id, shortage)
        )
        await callback.answer()

# Оплата и пополнение баланса
async def payment_options(user_id: int, amount_needed: float = None):
    builder = InlineKeyboardBuilder()
    
    if amount_needed:
        builder.button(
            text=f"💰 Пополнить на {amount_needed} RUB", 
            callback_data=f"topup_{amount_needed}"
        )
    else:
        builder.button(
            text="💰 Пополнить баланс", 
            callback_data="topup"
        )
    
    builder.button(text="📊 Мой баланс", callback_data="check_balance")
    builder.button(text="❌ Отмена", callback_data="main_menu")
    builder.adjust(1)
    
    return builder.as_markup()

# Модифицированная функция показа раундов
async def show_rounds(message: types.Message, edit: bool = False):
    cursor.execute("SELECT round_number, price FROM round_prices ORDER BY round_number")
    rounds = cursor.fetchall()
    
    if not rounds:
        text = "В магазине пока нет доступных раундов."
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="main_menu")
        
        if edit:
            await message.edit_text(text, reply_markup=builder.as_markup())
        else:
            await message.answer(text, reply_markup=builder.as_markup())
        return
    
    builder = InlineKeyboardBuilder()
    
    for round_data in rounds:
        round_number, price = round_data
        builder.button(
            text=f"Раунд {round_number} - {price} RUB",
            callback_data=f"round_{round_number}"
        )
    
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    
    text = "🎮 Доступные раунды Twitch Drops для Rust:\nВыберите раунд:"
    
    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())



# Запуск бота
async def main():
    await dp.start_polling(bot)
    await on_startup()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
    