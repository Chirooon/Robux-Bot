from playwright.sync_api import sync_playwright
import time
import re
from datetime import datetime, timezone, timedelta
import requests
import threading

# ========== CONFIGURATION ==========
DEFAULT_TARGET_PRICE = 0.004
DEFAULT_MIN_QUANTITY = 1000
CHECK_INTERVAL = 60
URL = "https://www.eldorado.gg/de/buy-robux/g/70-0-0"

# Your Telegram credentials
TELEGRAM_BOT_TOKEN = "8697997578:AAE1mixD1sXL-uo00qplXGlVH-PclR-iuTs"
TELEGRAM_CHAT_ID = "7254672806"

GERMAN_TZ = timezone(timedelta(hours=2))
QUANTITY_TOLERANCE = 0.50
# ===================================

TARGET_PRICE = DEFAULT_TARGET_PRICE
MIN_QUANTITY = DEFAULT_MIN_QUANTITY
daily_prices = []
daily_lowest = None
daily_best_offer = None
last_alerted_offers = set()
last_summary_date = None
last_update_id = 0
startup_message_sent = False

def send_telegram(message, delete_after=None, reply_to=None, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
            
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            print("  [Telegram OK]")
            
            if delete_after:
                threading.Timer(delete_after, lambda: delete_message(result['result']['message_id'])).start()
            return result
    except Exception as e:
        print(f"  [Telegram Error: {e}]")
    return None

def delete_message(message_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "message_id": message_id
        }, timeout=10)
    except Exception as e:
        print(f"  [Delete error: {e}]")

def check_for_commands():
    global TARGET_PRICE, MIN_QUANTITY, last_update_id
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        params = {"offset": last_update_id + 1, "timeout": 30}
        response = requests.get(url, params=params, timeout=35)
        
        if response.status_code == 200:
            updates = response.json().get('result', [])
            
            for update in updates:
                last_update_id = update['update_id']
                message = update.get('message', {})
                text = message.get('text', '')
                chat_id = message.get('chat', {}).get('id')
                
                if str(chat_id) == TELEGRAM_CHAT_ID:
                    handle_command(text)
    except Exception as e:
        print(f"  [Poll error: {e}]")

def handle_command(text):
    global TARGET_PRICE, MIN_QUANTITY, last_alerted_offers
    
    text = text.strip().lower()
    
    if text == '/help' or text == '/start':
        min_qty_str = f"{MIN_QUANTITY:,}".replace(',', '.') if MIN_QUANTITY else "No minimum"
        help_msg = f"""<b>Robux Price Tracker Commands</b>

/price - Show current target price
/setprice <value> - Set new target price
  Example: /setprice 0.0035

/min - Show current minimum quantity
/setmin <quantity> - Set minimum Robux (50% tolerance)
  Example: /setmin 1000
/setmin off - Disable minimum requirement

/help - Show this message

<b>Current settings:</b>
Target: €{format_price(TARGET_PRICE)}
Min Qty: {min_qty_str} Robux
Tolerance: ±50%"""
        
        send_telegram(help_msg)
        return
    
    if text == '/price':
        send_telegram(f"Current target: €{format_price(TARGET_PRICE)}")
        return
    
    if text == '/min':
        if MIN_QUANTITY:
            low = int(MIN_QUANTITY * 0.5)
            high = int(MIN_QUANTITY * 1.5)
            send_telegram(f"""Minimum Quantity Setting

Required: {MIN_QUANTITY:,} Robux
Tolerance: ±50%
Matches: {low:,} - {high:,} Robux""".replace(',', '.'))
        else:
            send_telegram(f"Minimum quantity: Disabled\nUse /setmin <quantity> to enable")
        return
    
    if text.startswith('/setprice'):
        parts = text.split()
        if len(parts) == 2:
            try:
                price_str = parts[1].replace(',', '.')
                new_price = float(price_str)
                
                if 0.001 <= new_price <= 0.05:
                    old_price = TARGET_PRICE
                    TARGET_PRICE = new_price
                    last_alerted_offers.clear()
                    
                    send_telegram(f"""Target price updated!

Old: €{format_price(old_price)}
New: €{format_price(TARGET_PRICE)}

I will alert when price ≤ €{format_price(TARGET_PRICE)}""")
                else:
                    send_telegram(f"Invalid price. Use 0.001 - 0.05\nExample: /setprice 0.0035")
            except:
                send_telegram(f"Invalid format. Example: /setprice 0.0035")
        return
    
    if text.startswith('/setmin'):
        parts = text.split()
        if len(parts) == 2:
            if parts[1].lower() == 'off':
                MIN_QUANTITY = None
                send_telegram(f"Minimum quantity disabled - I will alert based on price only")
            else:
                try:
                    new_min = int(parts[1])
                    if new_min >= 100:
                        MIN_QUANTITY = new_min
                        low = int(MIN_QUANTITY * 0.5)
                        high = int(MIN_QUANTITY * 1.5)
                        send_telegram(f"""Minimum quantity set!

Required: {MIN_QUANTITY:,} Robux
Tolerance: ±50%
Will match: {low:,} - {high:,} Robux""".replace(',', '.'))
                    else:
                        send_telegram(f"Minimum must be at least 100 Robux")
                except:
                    send_telegram(f"Invalid number. Example: /setmin 1000")
        return

def is_quantity_match(offer_qty):
    if MIN_QUANTITY is None or offer_qty is None:
        return True if MIN_QUANTITY is None else False
    
    lower_bound = int(MIN_QUANTITY * 0.5)
    upper_bound = int(MIN_QUANTITY * 1.5)
    
    return lower_bound <= offer_qty <= upper_bound

def get_offers():
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
            
            offers = []
            offer_blocks = re.split(r'<div[^>]*class="[^"]*offer[^"]*"[^>]*>', html, re.IGNORECASE)
            
            for block in offer_blocks:
                price_match = re.search(r'([\d.,]+)\s*&nbsp;€', block)
                if price_match:
                    price = float(price_match.group(1).replace(',', '.'))
                    if 0.001 < price < 0.05:
                        qty_match = re.search(r'Min\.\s*menge:</span>\s*(\d+)', block, re.IGNORECASE)
                        quantity = int(qty_match.group(1)) if qty_match else None
                        offers.append({'price_per_unit': price, 'min_quantity': quantity})
            
            unique_offers = []
            seen = set()
            for offer in offers:
                key = f"{offer['price_per_unit']}_{offer['min_quantity']}"
                if key not in seen:
                    seen.add(key)
                    unique_offers.append(offer)
            
            return sorted(unique_offers, key=lambda x: x['price_per_unit'])
            
    except Exception as e:
        print(f"  [Error: {e}]")
        return None

def format_price(price):
    return f"{price:.5f}".replace('.', ',')

def format_number(num):
    if num is None:
        return "?"
    return f"{num:,}".replace(',', '.')

def send_startup_status():
    global startup_message_sent
    
    print("\n[Startup] Getting current best offer...")
    offers = get_offers()
    
    if offers and len(offers) > 0:
        best = offers[0]
        price = best['price_per_unit']
        min_qty = best['min_quantity']
        
        message = f"""<b>Robux Price Tracker Active</b>

Current Best Offer:
Price: €{format_price(price)}
Minimum: {format_number(min_qty)} Robux

Your Settings:
Target: €{format_price(TARGET_PRICE)}"""
        
        if MIN_QUANTITY:
            min_match = is_quantity_match(min_qty)
            match_emoji = "✅" if min_match else "❌"
            message += f"\nMin Required: {format_number(MIN_QUANTITY)} Robux {match_emoji} (±50%)"
        
        if price <= TARGET_PRICE:
            message += f"\n\nStatus: Target price reached!"
        else:
            diff = format_price(TARGET_PRICE - price)
            message += f"\n\nNeed €{diff} lower to reach target"
        
        message += f"\n\nChecking every minute\n/help for commands"
        
        send_telegram(message)
        startup_message_sent = True
    else:
        send_telegram("Robux Tracker Started\n\nCould not fetch current prices. Will retry shortly.")
        startup_message_sent = True

def send_price_alert(offer):
    price = offer['price_per_unit']
    min_qty = offer['min_quantity']
    
    diff = TARGET_PRICE - price
    
    if diff > 0.001:
        emoji = "🔴🔥🔥"
    elif diff > 0.0005:
        emoji = "🟠🔥"
    elif diff > 0.0001:
        emoji = "🟡⚡"
    else:
        emoji = "🟢✅"
    
    message = f"""<b>{emoji} TARGET REACHED!</b>

Price: €{format_price(price)}
Target: €{format_price(TARGET_PRICE)}
Minimum: {format_number(min_qty)} Robux"""

    if MIN_QUANTITY:
        low = int(MIN_QUANTITY * 0.5)
        high = int(MIN_QUANTITY * 1.5)
        message += f"\nMatches your requirement: {format_number(MIN_QUANTITY)} Robux (±50% = {low}-{high})"
    
    message += f"\n\nBuy: {URL}"
    
    send_telegram(message)

def get_german_time():
    return datetime.now(GERMAN_TZ)

def should_send_summary():
    global last_summary_date
    now = get_german_time()
    current_date = now.strftime('%Y-%m-%d')
    is_summary_time = now.hour == 22 and now.minute < 5
    if is_summary_time and last_summary_date != current_date:
        last_summary_date = current_date
        return True
    return False

def send_daily_summary():
    global daily_prices, daily_lowest, daily_best_offer
    
    if not daily_prices:
        return
    
    now = get_german_time()
    date_str = now.strftime('%d.%m.%Y')
    
    lowest_today = min(daily_prices)
    highest_today = max(daily_prices)
    avg_price = sum(daily_prices) / len(daily_prices)
    
    message = f"""Daily Summary - {date_str}

Lowest:  €{format_price(lowest_today)}
Highest: €{format_price(highest_today)}
Average: €{format_price(avg_price)}
Scans:   {len(daily_prices)}

Target: €{format_price(TARGET_PRICE)}"""
    
    if daily_lowest and daily_lowest <= TARGET_PRICE:
        message += f"\n\nTarget reached today!"
        if daily_best_offer:
            message += f"\nBest: €{format_price(daily_best_offer['price_per_unit'])}"
            if daily_best_offer['min_quantity']:
                message += f"\nMin: {format_number(daily_best_offer['min_quantity'])} Robux"
    
    send_telegram(message, delete_after=60)
    
    daily_prices = []
    daily_lowest = None
    daily_best_offer = None

def main():
    global TARGET_PRICE, MIN_QUANTITY, daily_prices, daily_lowest, daily_best_offer, last_alerted_offers
    
    print("=" * 60)
    print("Robux Price Tracker")
    print(f"Target: €{format_price(TARGET_PRICE)}")
    print(f"Min Qty: {MIN_QUANTITY if MIN_QUANTITY else 'Disabled'} (50% tolerance)")
    print(f"Check interval: 1 minute")
    print("=" * 60)
    
    def poll_commands():
        while True:
            check_for_commands()
            time.sleep(0.5)
    
    command_thread = threading.Thread(target=poll_commands, daemon=True)
    command_thread.start()
    
    send_startup_status()
    
    while True:
        if should_send_summary():
            send_daily_summary()
        
        timestamp = get_german_time()
        print(f"\n[{timestamp.strftime('%H:%M:%S')}] Scanning...")
        
        offers = get_offers()
        
        if offers and len(offers) > 0:
            matching_offers = offers
            if MIN_QUANTITY:
                matching_offers = [o for o in offers if is_quantity_match(o['min_quantity'])]
            
            if matching_offers:
                best_offer = matching_offers[0]
                lowest_price = best_offer['price_per_unit']
                min_qty = best_offer['min_quantity']
                
                print(f"  Found {len(offers)} total, {len(matching_offers)} matching quantity")
                print(f"  Best matching: €{format_price(lowest_price)}")
                
                daily_prices.append(lowest_price)
                if daily_lowest is None or lowest_price < daily_lowest:
                    daily_lowest = lowest_price
                    daily_best_offer = best_offer
                
                offer_id = f"{lowest_price}_{min_qty}"
                
                if lowest_price <= TARGET_PRICE and offer_id not in last_alerted_offers:
                    send_price_alert(best_offer)
                    last_alerted_offers.add(offer_id)
                    print("  >>> TARGET REACHED - ALERT SENT <<<")
                elif lowest_price <= TARGET_PRICE:
                    print("  Target reached (already alerted)")
                else:
                    need = TARGET_PRICE - lowest_price
                    print(f"  Need €{format_price(need)} lower")
            else:
                print(f"  Found {len(offers)} offers, none match quantity requirement")
                if offers:
                    daily_prices.append(offers[0]['price_per_unit'])
        else:
            print("  No offers found")
        
        if len(last_alerted_offers) > 50:
            last_alerted_offers = set(list(last_alerted_offers)[-30:])
        
        next_time = (get_german_time() + timedelta(seconds=60)).strftime('%H:%M:%S')
        print(f"  Next: {next_time}")
        
        time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTracker stopped")
        send_telegram("Robux Tracker stopped", delete_after=30)
    except Exception as e:
        print(f"\nFATAL: {e}")
        send_telegram(f"Tracker crashed: {str(e)[:100]}", delete_after=60)