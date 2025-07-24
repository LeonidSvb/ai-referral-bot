import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import json
import os
import uuid

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "8406140567:AAFyqnlv0lhTcGqHRGg2Q_IaITamShEX_40"
ADMIN_ID = 6978852648  # Ваш Telegram ID
GOOGLE_SHEETS_URL = "1fBx6nVx1yd0KiW24j-BCqcVm5rcaydzqiV4DJ0IRGq4"  # ID Google таблицы
REFERRAL_LEVEL_1 = 0.10  # 10% за первый уровень
REFERRAL_LEVEL_2 = 0.05  # 5% за второй уровень
MIN_WITHDRAWAL = 1000    # Минимальная сумма для вывода

def log_to_console(event_type, user_id, username, amount=0, referrer_id=None, level=0, commission=0):
    """Логирование событий в консоль"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_message = f"[{timestamp}] {event_type}: User {username} ({user_id})"
    
    if amount:
        log_message += f", Amount: {amount}"
    if referrer_id:
        log_message += f", Referrer: {referrer_id}"
    if level:
        log_message += f", Level: {level}"
    if commission:
        log_message += f", Commission: {commission}"
    
    logger.info(log_message)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            referral_code TEXT UNIQUE,
            referrer_id INTEGER,
            referrer_level INTEGER DEFAULT 0,
            balance REAL DEFAULT 0,
            total_earned REAL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (referrer_id) REFERENCES users (user_id)
        )
    ''')
    
    # Таблица заказов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            processed_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Таблица реферальных начислений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referral_earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referee_id INTEGER,
            order_id INTEGER,
            level INTEGER,
            amount REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (referrer_id) REFERENCES users (user_id),
            FOREIGN KEY (referee_id) REFERENCES users (user_id),
            FOREIGN KEY (order_id) REFERENCES orders (id)
        )
    ''')
    
    # Таблица выплат
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            payment_method TEXT,
            payment_details TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            processed_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Функции для работы с базой данных
def get_user(user_id):
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id, username, first_name, referrer_id=None):
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    referral_code = str(uuid.uuid4())[:8]
    referrer_level = 0
    
    if referrer_id:
        # Определяем уровень реферера
        referrer = get_user(referrer_id)
        if referrer:
            referrer_level = referrer[5] + 1  # referrer_level + 1
    
    cursor.execute('''
        INSERT INTO users (user_id, username, first_name, referral_code, referrer_id, referrer_level)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, referral_code, referrer_id, referrer_level))
    
    conn.commit()
    conn.close()
    
    # Логируем в консоль
    log_to_console('Регистрация', user_id, username, referrer_id=referrer_id)
    
    return referral_code

def get_user_by_referral_code(referral_code):
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE referral_code = ?', (referral_code,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_referral_stats(user_id):
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    # Рефералы первого уровня
    cursor.execute('SELECT COUNT(*) FROM users WHERE referrer_id = ?', (user_id,))
    level1_count = cursor.fetchone()[0]
    
    # Рефералы второго уровня
    cursor.execute('''
        SELECT COUNT(*) FROM users u1 
        JOIN users u2 ON u1.user_id = u2.referrer_id 
        WHERE u2.referrer_id = ?
    ''', (user_id,))
    level2_count = cursor.fetchone()[0]
    
    # Общие заработки
    cursor.execute('SELECT SUM(amount) FROM referral_earnings WHERE referrer_id = ?', (user_id,))
    total_earned = cursor.fetchone()[0] or 0
    
    # Текущий баланс
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    balance = cursor.fetchone()[0] or 0
    
    conn.close()
    return level1_count, level2_count, total_earned, balance

def add_order(user_id, amount):
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO orders (user_id, amount) VALUES (?, ?)', (user_id, amount))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Логируем в консоль
    user = get_user(user_id)
    username = user[2] if user else 'Неизвестно'
    log_to_console('Заказ', user_id, username, amount=amount)
    
    return order_id

def process_referral_earnings(order_id, user_id, amount):
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    user = get_user(user_id)
    if not user or not user[4]:  # Нет реферера
        conn.close()
        return
    
    referrer_id = user[4]  # referrer_id
    level = 1
    
    while referrer_id and level <= 2:
        referrer = get_user(referrer_id)
        if not referrer:
            break
            
        # Расчет комиссии
        if level == 1:
            commission = amount * REFERRAL_LEVEL_1
        else:
            commission = amount * REFERRAL_LEVEL_2
        
        # Добавляем заработок
        cursor.execute('''
            INSERT INTO referral_earnings (referrer_id, referee_id, order_id, level, amount)
            VALUES (?, ?, ?, ?, ?)
        ''', (referrer_id, user_id, order_id, level, commission))
        
        # Обновляем баланс реферера
        cursor.execute('''
            UPDATE users SET balance = balance + ?, total_earned = total_earned + ?
            WHERE user_id = ?
        ''', (commission, commission, referrer_id))
        
        # Логируем в консоль
        referrer_user = get_user(referrer_id)
        referrer_username = referrer_user[2] if referrer_user else 'Неизвестно'
        log_to_console('Начисление', referrer_id, referrer_username, 
                      amount=amount, level=level, commission=commission)
        
        # Переходим на следующий уровень
        referrer_id = referrer[4]  # referrer_id следующего уровня
        level += 1
    
    conn.commit()
    conn.close()

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    
    # Проверяем реферальный код
    referrer_id = None
    if context.args:
        referral_code = context.args[0]
        referrer_id = get_user_by_referral_code(referral_code)
    
    # Проверяем, существует ли пользователь
    user = get_user(user_id)
    if not user:
        referral_code = create_user(user_id, username, first_name, referrer_id)
        welcome_text = f"""
🤖 Добро пожаловать в систему автоматизации с ИИ!

🎯 **Что мы предлагаем:**
Передовые решения для автоматизации бизнес-процессов с использованием искусственного интеллекта

💰 **Реферальная программа:**
• 1-й уровень: 10% от заказа
• 2-й уровень: 5% от заказа

🔗 **Ваша реферальная ссылка:**
`https://t.me/AILeoreferralbot?start={referral_code}`

Поделитесь ссылкой с друзьями и зарабатывайте с каждого их заказа!
        """
        
        if referrer_id:
            welcome_text += f"\n✅ Вы зарегистрированы по реферальной ссылке!"
    else:
        welcome_text = f"""
👋 С возвращением!

🔗 **Ваша реферальная ссылка:**
`https://t.me/AILeoreferralbot?start={user[3]}`
        """
    
    keyboard = [
        [KeyboardButton("📊 Моя статистика"), KeyboardButton("💰 Баланс")],
        [KeyboardButton("🔗 Реферальная ссылка"), KeyboardButton("💳 Вывод средств")],
        [KeyboardButton("📞 Поддержка"), KeyboardButton("ℹ️ О сервисе")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    level1, level2, total_earned, balance = get_referral_stats(user_id)
    
    stats_text = f"""
📊 **Ваша статистика:**

👥 Рефералы 1-го уровня: {level1}
👥 Рефералы 2-го уровня: {level2}
💰 Общий заработок: {total_earned:.2f} ₽
💳 Текущий баланс: {balance:.2f} ₽

💡 Минимальная сумма для вывода: {MIN_WITHDRAWAL} ₽
    """
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if user:
        balance_text = f"""
💰 **Ваш баланс: {user[6]:.2f} ₽**

📈 Всего заработано: {user[7]:.2f} ₽
💳 Доступно для вывода: {user[6]:.2f} ₽

💡 Минимальная сумма для вывода: {MIN_WITHDRAWAL} ₽
        """
        
        if user[6] >= MIN_WITHDRAWAL:
            keyboard = [[InlineKeyboardButton("💳 Вывести средства", callback_data="withdraw")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(balance_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(balance_text, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start для регистрации.")

async def referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if user:
        link_text = f"""
🔗 **Ваша реферальная ссылка:**

`https://t.me/AILeoreferralbot?start={user[3]}`

📋 Скопируйте и поделитесь с друзьями!

💰 **Ваши награды:**
• За каждый заказ реферала 1-го уровня: 10%
• За каждый заказ реферала 2-го уровня: 5%

🎯 Чем больше рефералов - тем больше заработок!
        """
        await update.message.reply_text(link_text, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start для регистрации.")

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_text = """
📞 **Поддержка**

По всем вопросам обращайтесь:
• Telegram: @support (замените на ваш username)
• Email: support@example.com

⏰ Время работы: 9:00 - 18:00 (МСК)
🕐 Среднее время ответа: 2-4 часа
    """
    await update.message.reply_text(support_text, parse_mode='Markdown')

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    about_text = """
ℹ️ **О сервисе автоматизации с ИИ**

🤖 Мы создаем решения для автоматизации бизнес-процессов с использованием современных технологий искусственного интеллекта.

🎯 **Наши услуги:**
• Автоматизация рутинных задач
• Интеграция ИИ в бизнес-процессы
• Разработка чат-ботов
• Аналитика и прогнозирование

💡 **Преимущества:**
• Экономия времени до 80%
• Снижение ошибок человеческого фактора
• Масштабируемые решения
• Индивидуальный подход

🚀 Присоединяйтесь к революции автоматизации!
    """
    await update.message.reply_text(about_text, parse_mode='Markdown')

async def handle_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user = get_user(user_id)
    
    if user and user[6] >= MIN_WITHDRAWAL:
        withdrawal_text = f"""
💳 **Вывод средств**

Доступно к выводу: {user[6]:.2f} ₽

📝 Для вывода средств отправьте сообщение в формате:
`ВЫВОД [сумма] [способ] [реквизиты]`

**Пример:**
`ВЫВОД 5000 карта 1234567890123456`

**Доступные способы:**
• карта - банковская карта
• яндекс - ЯндексДеньги
• qiwi - QIWI кошелек
        """
        await query.edit_message_text(withdrawal_text, parse_mode='Markdown')
    else:
        await query.edit_message_text("❌ Недостаточно средств для вывода.")

async def handle_withdrawal_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text.upper()
    
    if message_text.startswith('ВЫВОД'):
        parts = message_text.split()
        if len(parts) >= 4:
            try:
                amount = float(parts[1])
                payment_method = parts[2]
                payment_details = ' '.join(parts[3:])
                
                user_id = update.effective_user.id
                user = get_user(user_id)
                
                if user and user[6] >= amount >= MIN_WITHDRAWAL:
                    conn = sqlite3.connect('referral_bot.db')
                    cursor = conn.cursor()
                    
                    # Создаем заявку на вывод
                    cursor.execute('''
                        INSERT INTO withdrawals (user_id, amount, payment_method, payment_details)
                        VALUES (?, ?, ?, ?)
                    ''', (user_id, amount, payment_method, payment_details))
                    
                    # Списываем с баланса
                    cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
                    
                    conn.commit()
                    conn.close()
                    
                    # Логируем в консоль
                    log_to_console('Заявка на вывод', user_id, update.effective_user.first_name, 
                                  amount=amount)
                    
                    success_text = f"""
✅ **Заявка на вывод принята!**

💰 Сумма: {amount:.2f} ₽
💳 Способ: {payment_method}
📄 Реквизиты: {payment_details}

⏰ Обработка займет 1-3 рабочих дня.
                    """
                    await update.message.reply_text(success_text, parse_mode='Markdown')
                    
                    # Уведомление админа
                    if ADMIN_ID:
                        admin_text = f"""
🔔 **Новая заявка на вывод**

👤 Пользователь: {update.effective_user.first_name} (@{update.effective_user.username})
💰 Сумма: {amount:.2f} ₽
💳 Способ: {payment_method}
📄 Реквизиты: {payment_details}
                        """
                        try:
                            await context.bot.send_message(ADMIN_ID, admin_text, parse_mode='Markdown')
                        except:
                            pass
                else:
                    await update.message.reply_text("❌ Недостаточно средств или неверная сумма.")
            except ValueError:
                await update.message.reply_text("❌ Неверный формат суммы.")
        else:
            await update.message.reply_text("❌ Неверный формат. Используйте: ВЫВОД [сумма] [способ] [реквизиты]")

# Админ команды для обработки заказов
async def admin_add_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить заказ для пользователя: /add_order USER_ID AMOUNT"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if len(context.args) != 2:
        await update.message.reply_text("Использование: /add_order USER_ID AMOUNT")
        return
    
    try:
        user_id = int(context.args[0])
        amount = float(context.args[1])
        
        user = get_user(user_id)
        if not user:
            await update.message.reply_text("❌ Пользователь не найден")
            return
        
        # Добавляем заказ
        order_id = add_order(user_id, amount)
        
        # Обрабатываем реферальные начисления
        process_referral_earnings(order_id, user_id, amount)
        
        success_text = f"""
✅ **Заказ добавлен!**

👤 Пользователь: {user[2]} (ID: {user_id})
💰 Сумма заказа: {amount:.2f} ₽
📋 ID заказа: {order_id}

Реферальные начисления обработаны автоматически.
        """
        
        await update.message.reply_text(success_text, parse_mode='Markdown')
        
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Используйте числа для ID и суммы.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM orders WHERE status = "completed"')
    completed_orders = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(amount) FROM orders WHERE status = "completed"')
    total_revenue = cursor.fetchone()[0] or 0
    
    cursor.execute('SELECT SUM(amount) FROM referral_earnings')
    total_referral_paid = cursor.fetchone()[0] or 0
    
    cursor.execute('SELECT COUNT(*) FROM withdrawals WHERE status = "pending"')
    pending_withdrawals = cursor.fetchone()[0]
    
    conn.close()
    
    admin_text = f"""
📊 **Статистика системы**

👥 Всего пользователей: {total_users}
✅ Завершенных заказов: {completed_orders}
💰 Общая выручка: {total_revenue:.2f} ₽
💸 Выплачено рефералам: {total_referral_paid:.2f} ₽
⏳ Ожидают вывода: {pending_withdrawals}

**Команды:**
/add_order USER_ID AMOUNT - добавить заказ
/admin_orders - управление заказами
/admin_withdrawals - управление выплатами
    """
    
    await update.message.reply_text(admin_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "📊 Моя статистика":
        await stats(update, context)
    elif text == "💰 Баланс":
        await balance(update, context)
    elif text == "🔗 Реферальная ссылка":
        await referral_link(update, context)
    elif text == "💳 Вывод средств":
        await balance(update, context)
    elif text == "📞 Поддержка":
        await support(update, context)
    elif text == "ℹ️ О сервисе":
        await about(update, context)
    elif text.upper().startswith('ВЫВОД'):
        await handle_withdrawal_request(update, context)

def main():
    # Инициализация
    init_db()
    
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("admin_stats", admin_stats))
    application.add_handler(CommandHandler("add_order", admin_add_order))
    application.add_handler(CallbackQueryHandler(handle_withdrawal, pattern="withdraw"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запуск бота
    print("🤖 Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
