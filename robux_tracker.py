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
startup_sent = False

def send_telegram(message, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=10)
        if response.status_code == 200:
            print("  [Telegram OK]")
        else:
            print(f"  [Telegram Error: {response.status_code}]")
    except Exception as e:
        print(f"  [Telegram Error: {e}]")

def check_for_commands():
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
                    send_telegram(f"Minimum: {MIN_QUANTITY} Robux\nMatches: {low}-{high} (75% tolerance)")
                
                elif text == '/check':
                    send_telegram("🔍 Checking prices...")
                    offers = get_offers()
                    if offers and len(offers) > 0:
                        best = offers[0]
                        msg = f"Best offer in range:\n€{format_price(best['price'])} (min {format_number(best['quantity'])} Robux)"
                        if best['price'] <= TARGET_PRICE:
                            msg += "\n✅ Target reached!"
                        else:
                            diff = format_price(TARGET_PRICE - best['price'])
                            msg += f"\nNeed €{diff} lower"
                        send_telegram(msg)
                    else:
                        send_telegram(f"No offers found in range {int(MIN_QUANTITY*0.25)}-{int(MIN_QUANTITY*1.75)} Robux")
                
                elif text.startswith('/setprice'):
                    parts = text.split()
                    if len(parts) == 2:
                        try:
                            new_price = float(parts[1].replace(',', '.'))
                            if 0.001 <= new_price <= 0.05:
                                TARGET_PRICE = new_price
                                last_alerted_offers.clear()
                                send_telegram(f"Target updated: €{format_price(TARGET_PRICE)}")
                            else:
                                send_telegram("Invalid price. Use 0.001-0.05")
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
                                if new_min >= 100:
                                    MIN_QUANTITY = new_min
                                    last_alerted_offers.clear()
                                    low = int(MIN_QUANTITY * 0.25)
                                    high = int(MIN_QUANTITY * 1.75)
                                    send_telegram(f"Minimum set to {MIN_QUANTITY}\nMatches: {low}-{high} Robux")
                                else:
                                    send_telegram("Minimum must be at least 100")
                            except:
                                send_telegram("Invalid number. Example: /setmin 2000")
                
                elif text == '/help':
                    help_msg = """<b>Commands:</b>
/price - Show target
/setprice X - Set target
/min - Show min setting
/setmin X - Set min (75% tolerance)
/setmin off - Disable min
/check - Force price check
/help - This message

<b>How it works:</b>
Finds the CHEAPEST offer that matches your quantity range (75% tolerance). Skips offers with wrong minimum order."""
                    send_telegram(help_msg)
                
    except Exception as e:
        print(f"  [Poll error: {e}]")
    finally:
        processing_commands = False

def is_quantity_match(offer_qty):
    """Check if offer quantity matches with 75% tolerance"""
    if MIN_QUANTITY is None or offer_qty is None:
        return True if MIN_QUANTITY is None else False
    lower = int(MIN_QUANTITY * 0.25)
    upper = int(MIN_QUANTITY * 1.75)
    return lower <= offer_qty <= upper

def get_offers():
    """Get ALL offers, then filter to only those within quantity range"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            page = browser.new_page()
            
            print("  Loading page...")
            page.goto(URL, timeout=30000)
            page.wait_for_timeout(5000)  # Wait for dynamic content
            
            html = page.content()
            browser.close()
            
            all_offers = []
            
            # Extract price and quantity pairs
            # Pattern for price
            price_pattern = r'([\d.,]+)\s*&nbsp;€'
            prices = re.findall(price_pattern, html)
            
            # Pattern for min quantity
            quantity_pattern = r'Min\.\s*menge:</span>\s*(\d+)'
            quantities = re.findall(quantity_pattern, html, re.IGNORECASE)
            
            print(f"  Raw: {len(prices)} prices, {len(quantities)} quantities")
            
            # Match prices with quantities (they appear in same order on page)
            for i, price_str in enumerate(prices):
                try:
                    price = float(price_str.replace(',', '.'))
                    if 0.0001 < price < 0.05:  # Valid price range
                        quantity = int(quantities[i]) if i < len(quantities) else None
                        all_offers.append({'price': price, 'quantity': quantity})
                except:
                    continue
            
            # Remove duplicates
            unique = []
            seen = set()
            for o in all_offers:
                key = f"{o['price']}_{o['quantity']}"
                if key not in seen:
                    seen.add(key)
                    unique.append(o)
            
            # Sort by price (cheapest first)
            unique.sort(key=lambda x: x['price'])
            
            # FILTER: Only keep offers within quantity range
            if MIN_QUANTITY:
                filtered = [o for o in unique if is_quantity_match(o['quantity'])]
                print(f"  Total offers: {len(unique)}")
                print(f"  In range ({int(MIN_QUANTITY*0.25)}-{int(MIN_QUANTITY*1.75)}): {len(filtered)}")
                
                # Show what was filtered out
                if len(unique) > len(filtered):
                    for o in unique[:5]:
                        if not is_quantity_match(o['quantity']):
                            print(f"    Skipped: €{format_price(o['price'])} (min {format_number(o['quantity'])} Robux) - outside range")
                
                return filtered
            return unique
            
    except Exception as e:
        print(f"  [Playwright error: {e}]")
        return None

def format_price(p):
    return f"{p:.5f}".replace('.', ',')

def format_number(n):
    if n is None:
        return "?"
    return f"{n:,}".replace(',', '.')

def send_startup_message():
    global startup_sent
    
    print("\n[Startup] Fetching current offers...")
    send_telegram("🤖 Robux Tracker Starting...")
    
    time.sleep(3)
    offers = get_offers()
    
    if offers and len(offers) > 0:
        best = offers[0]
        low = int(MIN_QUANTITY * 0.25)
        high = int(MIN_QUANTITY * 1.75)
        
        message = f"""<b>✅ Robux Tracker Active</b>

<b>Settings:</b>
Target: €{format_price(TARGET_PRICE)}
Min required: {MIN_QUANTITY} Robux (matches {low}-{high})

<b>Best offer IN YOUR RANGE:</b>
Price: €{format_price(best['price'])}
Min order: {format_number(best['quantity'])} Robux"""

        if best['price'] <= TARGET_PRICE:
            message += "\n\n🎯 Target already reached!"
        else:
            diff = format_price(TARGET_PRICE - best['price'])
            message += f"\n\n📉 Need €{diff} lower"
        
        message += "\n\n/help for commands"
        send_telegram(message)
    else:
        send_telegram(f"⚠️ Robux Tracker Started\n\nNo offers found within {int(MIN_QUANTITY*0.25)}-{int(MIN_QUANTITY*1.75)} Robux range.\nWill keep scanning.\n\n/help for commands")
    
    startup_sent = True

def send_alert(offer):
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

💰 Price: €{format_price(price)}
🎯 Target: €{format_price(TARGET_PRICE)}
📦 Minimum: {format_number(qty)} Robux

✅ Matches your requirement: {MIN_QUANTITY} Robux (range {low}-{high})

🛒 Buy: {URL}"""
    
    send_telegram(message)

def get_german_time():
    return datetime.now(GERMAN_TZ)

def main():
    global last_alerted_offers, running, startup_sent
    
    print("=" * 60)
    print("Robux Price Tracker - Finds best offer IN your quantity range")
    print(f"Target: €{format_price(TARGET_PRICE)}")
    print(f"Min Required: {MIN_QUANTITY} Robux (75% tolerance = {int(MIN_QUANTITY*0.25)}-{int(MIN_QUANTITY*1.75)})")
    print(f"Check interval: {CHECK_INTERVAL} seconds")
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
    scan_count = 0
    
    while running:
        try:
            now = get_german_time()
            current_date = now.strftime('%Y-%m-%d')
            
            # Daily summary at 22:00
            if now.hour == 22 and now.minute < 5 and last_summary_date != current_date:
                send_telegram(f"📊 Daily Summary - Target: €{format_price(TARGET_PRICE)} | Min: {MIN_QUANTITY} Robux")
                last_summary_date = current_date
            
            print(f"\n[{now.strftime('%H:%M:%S')}] Scan #{scan_count + 1}")
            offers = get_offers()
            
            if offers and len(offers) > 0:
                best = offers[0]
                print(f"  Best in range: €{format_price(best['price'])} (min {format_number(best['quantity'])} Robux)")
                
                # Show top 3 in range
                for i, o in enumerate(offers[:3], 1):
                    print(f"    {i}. €{format_price(o['price'])} - min {format_number(o['quantity'])}")
                
                # Check alert
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
            
            scan_count += 1
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(60)

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
