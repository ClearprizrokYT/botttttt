import asyncio
import aiohttp
import os
import time
import json
import re
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from aiohttp import web

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

last_price = 0.0
SUBSCRIBERS_FILE = "subscribers.json"
waiting_for_message = set()


def load_subscribers() -> set:
    try:
        if os.path.exists(SUBSCRIBERS_FILE):
            with open(SUBSCRIBERS_FILE, "r") as f:
                data = json.load(f)
                return set(data)
    except Exception as e:
        print(f"Ошибка загрузки подписчиков: {e}")
    return set()

def save_subscribers(subscribers: set):
    try:
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(list(subscribers), f)
    except Exception as e:
        print(f"Ошибка сохранения подписчиков: {e}")

subscribers = load_subscribers()


def get_keyboard(is_subscribed: bool) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🔄 Текущая цена BTC", callback_data="price")],
    ]
    
    if is_subscribed:
        buttons.append([InlineKeyboardButton(text="🔕Отписаться", callback_data="unsubscribe")])
    else:
        buttons.append([InlineKeyboardButton(text="🔔Подписаться", callback_data="subscribe")])
    
    buttons.append([InlineKeyboardButton(text="📊 Статистика", callback_data="stats")])
    buttons.append([InlineKeyboardButton(text="✉️ Сообщение", callback_data="send_message")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отмена", callback_data="cancel_message")]
    ])


async def get_server_time() -> str:
    url = "https://time100.ru/Moscow"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    time_element = soup.find(id="clockTime")
                    if time_element:
                        return time_element.get_text(strip=True)
                    
                    for class_name in ["time", "clock", "current-time"]:
                        time_element = soup.find(class_=re.compile(class_name, re.I))
                        if time_element:
                            time_match = re.search(r'\d{1,2}:\d{2}(:\d{2})?', time_element.get_text())
                            if time_match:
                                return time_match.group()
                    
                    page_text = soup.get_text()
                    time_match = re.search(r'\b(\d{1,2}:\d{2}:\d{2})\b', page_text)
                    if time_match:
                        return time_match.group(1)
                        
    except Exception as e:
        print(f"Ошибка времени: {e}")
    
    return time.strftime("%H:%M:%S")


async def get_btc_price() -> float:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 429:
                    print("Лимит API!")
                    return 0.0
                if resp.status != 200:
                    return 0.0
                data = await resp.json()
                price = float(data.get("bitcoin", {}).get("usd", 0.0))
                print(f"💰 Цена BTC: ${price:,.2f}")
                return price
    except Exception as e:
        print(f"Ошибка: {e}")
        return 0.0


def log_user_action(action: str):
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"\n{'='*50}")
    print(f"⏰ {current_time}")
    print(f"🎯 {action}")
    print(f"{'='*50}\n")

def log_user_message(message_text: str):
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"\n{'🔔'*20}")
    print(f"📩 НОВОЕ СООБЩЕНИЕ!")
    print(f"⏰ {current_time}")
    print(f"{'─'*40}")
    print(f"💬 {message_text}")
    print(f"{'🔔'*20}\n")


async def broadcast(text: str, parse_mode: str = None):
    global subscribers
    dead_subscribers = set()
    
    for chat_id in subscribers.copy():
        try:
            keyboard = get_keyboard(is_subscribed=True)
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode=parse_mode)
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Ошибка отправки {chat_id}: {e}")
            if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
                dead_subscribers.add(chat_id)
    
    if dead_subscribers:
        subscribers -= dead_subscribers
        save_subscribers(subscribers)


async def hourly_job():
    global last_price
    
    while True:
        try:
            if not subscribers:
                print("Нет подписчиков")
                await asyncio.sleep(3600)
                continue
                
            price = await get_btc_price()
            if price == 0.0:
                await asyncio.sleep(60)
                continue

            server_time = await get_server_time()
            text = f"⏰ {server_time} | Bitcoin = {price:,.2f} $".replace(",", " ")
            await broadcast(text)

            if last_price != 0.0:
                change = (price - last_price) / last_price * 100
                if abs(change) >= 2.0:
                    arrow = "📈" if change > 0 else "📉"
                    alert_text = (
                        f"{arrow} <b>BTC {'+' if change > 0 else ''}{change:.2f}%</b>\n"
                        f"Было: {last_price:,.2f} $\n"
                        f"Сейчас: {price:,.2f} $"
                    )
                    await broadcast(alert_text, parse_mode="HTML")
            
            last_price = price
            print(f"✅Рассылка: {len(subscribers)} подписчиков")

        except Exception as e:
            print(f"Ошибка: {e}")

        await asyncio.sleep(3600)


@router.message(Command("start"))
async def start(message: Message):
    global subscribers, waiting_for_message
    
    waiting_for_message.discard(message.chat.id)
    log_user_action("Запустил бота")
    
    chat_id = message.chat.id
    is_new = chat_id not in subscribers
    
    subscribers.add(chat_id)
    save_subscribers(subscribers)
    
    price = await get_btc_price()
    
    if is_new:
        welcome = "<b>Добро пожаловать!</b>\n\n"
    else:
        welcome = "<b>С возвращением!</b>\n\n"
    
    if price == 0.0:
        text = welcome + "Сейчас не могу получить цену, попробуйте позже."
    else:
        text = welcome + f"Bitcoin: <b>{price:,.2f} $</b>".replace(",", " ")
    
    await message.answer(text, reply_markup=get_keyboard(True), parse_mode="HTML")


@router.message(F.text)
async def handle_text_message(message: Message):
    global waiting_for_message
    
    chat_id = message.chat.id
    
    if chat_id in waiting_for_message:
        waiting_for_message.discard(chat_id)
        log_user_message(message.text)
        
        is_subscribed = chat_id in subscribers
        await message.answer(
            "✅ <b>Сообщение получено!</b>",
            reply_markup=get_keyboard(is_subscribed),
            parse_mode="HTML"
        )
    else:
        is_subscribed = chat_id in subscribers
        await message.answer(
            "Используйте кнопки:",
            reply_markup=get_keyboard(is_subscribed)
        )


@router.callback_query(F.data == "price")
async def show_price(callback: CallbackQuery):
    log_user_action("Запросил цену")
    await callback.answer("⏳ Загружаю...", cache_time=0)
    
    price = await get_btc_price()
    server_time = await get_server_time()
    is_subscribed = callback.message.chat.id in subscribers

    if price == 0.0:
        text = "Не удалось получить цену"
    else:
        text = f"<b>Bitcoin: {price:,.2f} $</b>\n🕐 {server_time}".replace(",", " ")

    await callback.message.answer(text, reply_markup=get_keyboard(is_subscribed), parse_mode="HTML")

@router.callback_query(F.data == "subscribe")
async def subscribe_user(callback: CallbackQuery):
    global subscribers
    
    log_user_action("Подписался🔔")
    
    chat_id = callback.message.chat.id
    subscribers.add(chat_id)
    save_subscribers(subscribers)
    
    await callback.answer("✅ Подписка оформлена!", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=get_keyboard(True))

@router.callback_query(F.data == "unsubscribe")
async def unsubscribe_user(callback: CallbackQuery):
    global subscribers
    
    log_user_action("Отписался🔕")
    
    chat_id = callback.message.chat.id
    subscribers.discard(chat_id)
    save_subscribers(subscribers)
    
    await callback.answer("🔕 Вы отписались", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=get_keyboard(False))

@router.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    log_user_action("Статистика")
    await callback.answer(cache_time=0)
    
    is_subscribed = callback.message.chat.id in subscribers
    
    text = f"📊 <b>Статистика</b>\n\n"
    text += f"👥 Подписчиков: {len(subscribers)}\n"
    if last_price:
        text += f"Цена: {last_price:,.2f} $\n".replace(",", " ")
    text += f"📱 Вы: {'подписаны ✅' if is_subscribed else 'не подписаны ❌'}"
    
    await callback.message.answer(text, reply_markup=get_keyboard(is_subscribed), parse_mode="HTML")

@router.callback_query(F.data == "send_message")
async def request_message(callback: CallbackQuery):
    global waiting_for_message
    
    log_user_action("Пишет сообщение")
    
    chat_id = callback.message.chat.id
    waiting_for_message.add(chat_id)
    
    await callback.answer(cache_time=0)
    await callback.message.answer(
        "✉️ <b>Напишите сообщение:</b>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "cancel_message")
async def cancel_message(callback: CallbackQuery):
    global waiting_for_message
    
    chat_id = callback.message.chat.id
    waiting_for_message.discard(chat_id)
    is_subscribed = chat_id in subscribers
    
    await callback.answer("Отменено", cache_time=0)
    await callback.message.answer("❌ Отменено", reply_markup=get_keyboard(is_subscribed))


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    dp.include_router(router)
    asyncio.create_task(hourly_job())
    print(f"Бот запущен! Подписчиков: {len(subscribers)}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

async def handle(request):
    return web.Response(text="Бот работает 24/7!")

async def main():
    app = web.Application()
    app.router.add_get('/', handle)
    
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    
    await asyncio.gather(
        site.start(),
        dp.start_polling(bot)
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass