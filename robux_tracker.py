from playwright.sync_api import sync_playwright
import time
import re
from datetime import datetime, timezone, timedelta
import requests
import threading

# ========== CONFIGURATION ==========
TARGET_PRICE = 0.004
MIN_QUANTITY = 1000
CHECK_INTERVAL = 60
URL = "https://www.eldorado.gg/de/buy-robux/g/70-0-0"

# Telegram credentials
TELEGRAM_BOT_TOKEN = "8697997578:AAE1mixD1sXL-uo00qplXGlVH-PclR-iuTs"
TELEGRAM_CHAT_ID = "7254672806"

GERMAN_TZ = timezone(timedelta(hours=2))
QUANTITY_TOLERANCE = 0.75
# ===================================

last_alerted_offers = set()
last_update_id = 0
running = True
processing_commands = False
bot_message_ids = []  # Track bot message IDs

def send_telegram(message, parse_mode="HTML"):
    """Send message and track its ID for potential deletion"""
    global bot_message_ids
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            message_id = result['result']['message_id']
            bot_message_ids.append(message_id)
            # Keep only last 200 IDs
            if len(bot_message_ids) > 200:
                bot_message_ids = bot_message_ids[-100:]
            print("  [Telegram OK]")
        else:
            print(f"  [Telegram Error: {response.status_code}]")
    except Exception as e:
        print(f"  [Telegram Error: {e}]")

def delete_bot_message(message_id):
    """Delete a specific bot message"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
    try:
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "message_id": message_id
        }, timeout=10)
        return response.status_code == 200
    except:
        return False

def clear_bot_messages():
    """Delete ALL messages ever sent by the bot"""
    global bot_message_ids
    
    if not bot_message_ids:
        send_telegram("No bot messages to delete")
        return
    
    send_telegram(f"Cleaning {len(bot_message_ids)} bot messages...")
    
    deleted = 0
    for msg_id in bot_message_ids:
        if delete_bot_message(msg_id):
            deleted += 1
        time.sleep(0.1)  # Rate limit to avoid hitting Telegram limits
    
    bot_message_ids = []
    # Send confirmation that will also be tracked (but user will see it briefly)
    send_telegram(f"Cleaned {deleted} messages", delete_after=3)

def check_for_commands():
    """Check for Telegram commands"""
    global TARGET_PRICE, MIN_QUANTITY, last_update_id, last_alerted_offers, processing_commands
    
    if processing_commands:
        return
    
    processing_commands = True
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        params = {"offset": last_update_id + 1, "timeout": 10}
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            updates = response.json().get('result', [])
            
            for update in updates:
                last_update_id = update['update_id']
                message = update.get('message', {})
                text = message.get('text', '')
                chat_id = message.get('chat', {}).get('id')
                
                if str(chat_id) != TELEGRAM_CHAT_ID:
                    continue
                
                text = text.strip().lower()
                print(f"  [Command: {text}]")
                
                if text == '/price':
                    send_telegram(f"Current target: €{format_price(TARGET_PRICE)}")
                
                elif text == '/min':
                    low = int(MIN_QUANTITY * 0.25)
                    high = int(MIN_QUANTITY * 1.75)
                    send_telegram(f"Minimum quantity: {MIN_QUANTITY} Robux\nMatches: {low} - {high} Robux (75% tolerance)")
                
                elif text == '/clean' or text == '/cls':
                    # This will delete all bot messages (not user messages)
                    clear_bot_messages()
                
                elif text.startswith('/setprice'):
                    parts = text.split()
                    if len(parts) == 2:
                        try:
                            new_price = float(parts[1].replace(',', '.'))
                            if 0.001 <= new_price <= 0.05 and new_price != TARGET_PRICE:
                                TARGET_PRICE = new_price
                                last_alerted_offers.clear()
                                send_telegram(f"Target updated: €{format_price(TARGET_PRICE)}")
                            elif new_price == TARGET_PRICE:
                                pass
                            else:
                                send_telegram("Invalid price. Use 0.001 - 0.05")
                        except:
                            send_telegram("Invalid format. Example: /setprice 0.0035")
                
                elif text.startswith('/setmin'):
                    parts = text.split()
                    if len(parts) == 2:
                        if parts[1] == 'off':
                            MIN_QUANTITY = None
                            last_alerted_offers.clear()
                            send_telegram("Minimum quantity disabled")
                        else:
                            try:
                                new_min = int(parts[1])
                                if new_min >= 100 and new_min != MIN_QUANTITY:
                                    MIN_QUANTITY = new_min
                                    last_alerted_offers.clear()
                                    low = int(MIN_QUANTITY * 0.25)
                                    high = int(MIN_QUANTITY * 1.75)
                                    send_telegram(f"Minimum set to {MIN_QUANTITY} Robux\nWill match: {low} - {high} Robux")
                                elif new_min == MIN_QUANTITY:
                                    pass
                                else:
                                    send_telegram("Minimum must be at least 100")
                            except:
                                send_telegram("Invalid number. Example: /setmin 2000")
                
                elif text == '/help':
                    help_msg = """<b>Commands:</b>

/price - Show target price
/setprice X - Set new target (eg: /setprice 0.0035)

/min - Show min quantity setting
/setmin X - Set min quantity (75% tolerance)
/setmin off - Disable min quantity

/clean or /cls - Delete all bot messages

/help - This message"""

                    send_telegram(help_msg)
                
    except Exception as e:
        print(f"  [Poll error: {e}]")
    finally:
        processing_commands = False

def is_quantity_match(offer_qty):
    """Check if quantity matches with 75% tolerance"""
    if MIN_QUANTITY is None or offer_qty is None:
        return True if MIN_QUANTITY is None else False
    
    lower = int(MIN_QUANTITY * 0.25)
    upper = int(MIN_QUANTITY * 1.75)
    
    return lower <= offer_qty <= upper

def get_offers():
    """Get offers - filter by quantity"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()
            page.goto(URL, timeout=30000)
            page.wait_for_selector('.buy-unit-price', timeout=15000)
            page.wait_for_timeout(2000)
            
            html = page.content()
            browser.close()
            
            all_offers = []
            offer_blocks = re.split(r'<div[^>]*class="[^"]*offer[^"]*"[^>]*>', html, re.IGNORECASE)
            
            for block in offer_blocks:
                price_match = re.search(r'([\d.,]+)\s*&nbsp;€', block)
                if price_match:
                    price = float(price_match.group(1).replace(',', '.'))
                    if 0.0001 < price < 0.05:  # Made more flexible - any price okay
                        qty_match = re.search(r'Min\.\s*menge:</span>\s*(\d+)', block, re.IGNORECASE)
                        quantity = int(qty_match.group(1)) if qty_match else None
                        all_offers.append({'price': price, 'quantity': quantity})
            
            # Remove duplicates
            unique = []
            seen = set()
            for o in all_offers:
                key = f"{o['price']}_{o['quantity']}"
                if key not in seen:
                    seen.add(key)
                    unique.append(o)
            
            unique.sort(key=lambda x: x['price'])
            
            if MIN_QUANTITY:
                filtered = [o for o in unique if is_quantity_match(o['quantity'])]
                return filtered
            return unique
            
    except Exception as e:
        print(f"  [Error: {e}]")
        return None

def format_price(p):
    return f"{p:.5f}".replace('.', ',')

def format_number(n):
    if n is None:
        return "?"
    return f"{n:,}".replace(',', '.')

def send_startup_message():
    """Send startup message"""
    print("\n[Starting] Getting current offers...")
    offers = get_offers()
    
    if offers and len(offers) > 0:
        best = offers[0]
        low = int(MIN_QUANTITY * 0.25)
        high = int(MIN_QUANTITY * 1.75)
        
        message = f"""<b>🤖 Robux Tracker Started</b>

<b>Settings:</b>
Target: €{format_price(TARGET_PRICE)}
Minimum: {MIN_QUANTITY} Robux (matches {low}-{high})

<b>Current best offer:</b>
Price: €{format_price(best['price'])}
Minimum: {format_number(best['quantity'])} Robux"""

        if best['price'] <= TARGET_PRICE:
            message += "\n\n✅ Target already reached!"
        else:
            diff = format_price(TARGET_PRICE - best['price'])
            message += f"\n\nNeed €{diff} lower"
        
        send_telegram(message)
    else:
        send_telegram("Robux Tracker Started - Scanning every minute")

def send_alert(offer):
    """Send price alert"""
    price = offer['price']
    qty = offer['quantity']
    
    diff = TARGET_PRICE - price
    
    if diff > 0.001:
        emoji = "🔴🔥🔥"
    elif diff > 0.0005:
        emoji = "🟠🔥"
    elif diff > 0.0001:
        emoji = "🟡⚡"
    else:
        emoji = "🟢✅"
    
    low = int(MIN_QUANTITY * 0.25)
    high = int(MIN_QUANTITY * 1.75)
    
    message = f"""<b>{emoji} TARGET REACHED!</b>

Price: €{format_price(price)}
Target: €{format_price(TARGET_PRICE)}
Minimum: {format_number(qty)} Robux

Matches your requirement: {MIN_QUANTITY} Robux (range: {low}-{high})

Buy: {URL}"""
    
    send_telegram(message)

def get_german_time():
    return datetime.now(GERMAN_TZ)

def send_daily_summary():
    """Send summary at 22:00 German time"""
    now = get_german_time()
    if now.hour == 22 and now.minute < 5:
        send_telegram(f"Daily summary at {now.strftime('%d.%m.%Y %H:%M')}\nTarget: €{format_price(TARGET_PRICE)}\nMin: {MIN_QUANTITY} Robux")
        return True
    return False

def main():
    global last_alerted_offers
    
    print("=" * 60)
    print("Robux Price Tracker")
    print(f"Target: €{format_price(TARGET_PRICE)}")
    print(f"Min Quantity: {MIN_QUANTITY} Robux (75% tolerance = {int(MIN_QUANTITY*0.25)}-{int(MIN_QUANTITY*1.75)})")
    print(f"Check every: {CHECK_INTERVAL} seconds")
    print("Commands: /price, /setprice, /min, /setmin, /clean, /help")
    print("=" * 60)
    
    # Start command polling
    def poll():
        while running:
            check_for_commands()
            time.sleep(1)
    
    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    
    # Send startup message
    send_startup_message()
    
    last_summary_date = None
    
    while running:
        # Daily summary at 22:00
        now = get_german_time()
        current_date = now.strftime('%Y-%m-%d')
        if now.hour == 22 and now.minute < 5 and last_summary_date != current_date:
            send_daily_summary()
            last_summary_date = current_date
        
        # Scan
        print(f"\n[{now.strftime('%H:%M:%S')}] Scanning...")
        
        offers = get_offers()
        
        if offers and len(offers) > 0:
            best = offers[0]
            print(f"  Found {len(offers)} offer(s) within {int(MIN_QUANTITY*0.25)}-{int(MIN_QUANTITY*1.75)} range")
            print(f"  Best: €{format_price(best['price'])} (min {format_number(best['quantity'])} Robux)")
            
            for i, o in enumerate(offers[:3], 1):
                print(f"    {i}. €{format_price(o['price'])} - min {format_number(o['quantity'])}")
            
            offer_id = f"{best['price']}_{best['quantity']}"
            if best['price'] <= TARGET_PRICE and offer_id not in last_alerted_offers:
                send_alert(best)
                last_alerted_offers.add(offer_id)
                print("  >>> ALERT SENT <<<")
            elif best['price'] <= TARGET_PRICE:
                print("  Target reached (already alerted)")
            else:
                need = TARGET_PRICE - best['price']
                print(f"  Need €{format_price(need)} lower")
        else:
            print(f"  No offers in range {int(MIN_QUANTITY*0.25)}-{int(MIN_QUANTITY*1.75)} Robux")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping...")
        send_telegram("Robux Tracker stopped")
        running = False
    except Exception as e:
        print(f"\nFATAL: {e}")
        send_telegram(f"Tracker crashed: {str(e)[:100]}")