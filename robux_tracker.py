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
                    # Force a fresh browser instance
                    offers = get_offers(force_fresh=True)
                    if offers and len(offers) > 0:
                        best = offers[0]
                        msg = f"Best offer:\n€{format_price(best['price'])} (min {format_number(best['quantity'])} Robux)"
                        if best['price'] <= TARGET_PRICE:
                            msg += "\n✅ Target reached!"
                        else:
                            diff = format_price(TARGET_PRICE - best['price'])
                            msg += f"\nNeed €{diff} lower"
                        send_telegram(msg)
                    else:
                        send_telegram("No offers found. The website may be blocking requests or the structure changed.")
                
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

<b>Troubleshooting:</b>
If /check returns no offers, the website may be blocking the bot.
The bot will automatically retry with different settings."""
                    send_telegram(help_msg)
                
    except Exception as e:
        print(f"  [Poll error: {e}]")
    finally:
        processing_commands = False

def is_quantity_match(offer_qty):
    if MIN_QUANTITY is None or offer_qty is None:
        return True if MIN_QUANTITY is None else False
    lower = int(MIN_QUANTITY * 0.25)
    upper = int(MIN_QUANTITY * 1.75)
    return lower <= offer_qty <= upper

def get_offers(force_fresh=False):
    """Get offers with anti-detection measures"""
    try:
        # Rotate user agents
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]
        
        import random
        user_agent = random.choice(user_agents)
        
        with sync_playwright() as p:
            # Launch with stealth settings
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--disable-gpu',
                    '--window-size=1920,1080'
                ]
            )
            
            context = browser.new_context(
                user_agent=user_agent,
                viewport={'width': 1920, 'height': 1080},
                locale='de-DE',
                timezone_id='Europe/Berlin'
            )
            
            page = context.new_page()
            
            print("  Loading page...")
            
            # Add random delay to avoid detection
            time.sleep(random.uniform(1, 2))
            
            page.goto(URL, timeout=45000, wait_until='domcontentloaded')
            
            # Wait random time
            time.sleep(random.uniform(3, 5))
            
            # Scroll to simulate human behavior
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(1)
            
            html = page.content()
            browser.close()
            
            # Try multiple patterns to find prices
            offers = []
            
            # Pattern for the specific structure you showed earlier
            patterns = [
                # Pattern from your HTML: <strong ...> 0,00445&nbsp;€ </strong>
                r'<strong[^>]*>[\s]*([\d.,]+)&nbsp;€[\s]*</strong>[\s]*<[^>]*>[\s]*/[\s]*Einheit',
                # Generic price with €
                r'([\d.,]+)\s*(?:&nbsp;)?\s*€',
                # Price in text
                r'€\s*([\d.,]+)',
            ]
            
            all_prices = []
            for pattern in patterns:
                matches = re.findall(pattern, html)
                for match in matches:
                    try:
                        price = float(match.replace(',', '.'))
                        if 0.0001 < price < 0.05:
                            all_prices.append(price)
                    except:
                        pass
            
            # Remove duplicates and sort
            all_prices = sorted(set(all_prices))
            
            # Find quantities
            qty_patterns = [
                r'Min\.\s*menge:</span>\s*(\d+)',
                r'minimum[\s]*order[\s]*:[\s]*(\d+)',
                r'menge:</span>\s*(\d+)',
            ]
            
            quantities = []
            for pattern in qty_patterns:
                matches = re.findall(pattern, html, re.IGNORECASE)
                for match in matches:
                    try:
                        quantities.append(int(match))
                    except:
                        pass
            
            # Build offers
            for i, price in enumerate(all_prices):
                qty = quantities[i] if i < len(quantities) else None
                offers.append({'price': price, 'quantity': qty})
            
            # If still no offers, try container-based extraction
            if not offers:
                containers = re.split(r'<div[^>]*class="[^"]*[Oo]ffer[^"]*"[^>]*>', html)
                for container in containers:
                    price_match = re.search(r'([\d.,]+)\s*(?:&nbsp;)?\s*€', container)
                    qty_match = re.search(r'(\d+)\s*(?:Robux|Einheit|unit)', container, re.IGNORECASE)
                    if price_match:
                        try:
                            price = float(price_match.group(1).replace(',', '.'))
                            qty = int(qty_match.group(1)) if qty_match else None
                            if 0.0001 < price < 0.05:
                                offers.append({'price': price, 'quantity': qty})
                        except:
                            pass
            
            # Remove duplicates
            unique = []
            seen = set()
            for o in offers:
                key = f"{o['price']}_{o['quantity']}"
                if key not in seen:
                    seen.add(key)
                    unique.append(o)
            
            unique.sort(key=lambda x: x['price'])
            
            print(f"  Found {len(unique)} total offers")
            
            # Filter by quantity
            if MIN_QUANTITY and unique:
                filtered = [o for o in unique if is_quantity_match(o['quantity'])]
                print(f"  {len(filtered)} offers within range")
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
    global startup_sent
    
    print("\n[Startup] Scanning for offers...")
    send_telegram("🤖 Robux Tracker Starting...\nScanning every minute.\n/help for commands")
    
    offers = get_offers()
    
    if offers and len(offers) > 0:
        best = offers[0]
        
        message = f"""<b>✅ Robux Tracker Active</b>

<b>Settings:</b>
Target: €{format_price(TARGET_PRICE)}
Min required: {MIN_QUANTITY} Robux (75% tolerance)

<b>Current best offer:</b>
Price: €{format_price(best['price'])} per Robux
Min order: {format_number(best['quantity'])} Robux"""

        if best['price'] <= TARGET_PRICE:
            message += "\n\n🎯 TARGET ALREADY REACHED!"
        else:
            diff = format_price(TARGET_PRICE - best['price'])
            message += f"\n\nNeed €{diff} lower to reach target"
        
        send_telegram(message)
    else:
        send_telegram(f"⚠️ Robux Tracker Started\n\nCould not find any offers.\nThe website may have changed or is blocking requests.\n\nTry /check again in a few minutes.")
    
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
    
    message = f"""<b>{emoji} TARGET REACHED!</b>

Price: €{format_price(price)} per Robux
Target: €{format_price(TARGET_PRICE)}
Minimum order: {format_number(qty)} Robux

Buy: {URL}"""
    
    send_telegram(message)

def get_german_time():
    return datetime.now(GERMAN_TZ)

def main():
    global last_alerted_offers, running
    
    print("=" * 60)
    print("Robux Price Tracker - Anti-Detection Mode")
    print(f"Target: €{format_price(TARGET_PRICE)}")
    print(f"Min Quantity: {MIN_QUANTITY} Robux (75% tolerance)")
    print(f"Check interval: {CHECK_INTERVAL} seconds")
    print("=" * 60)
    
    # Start command polling
    def poll():
        while running:
            check_for_commands()
            time.sleep(1)
    
    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    
    send_startup_message()
    
    last_summary_date = None
    scan_count = 0
    consecutive_failures = 0
    
    while running:
        try:
            now = get_german_time()
            current_date = now.strftime('%Y-%m-%d')
            
            if now.hour == 22 and now.minute < 5 and last_summary_date != current_date:
                send_telegram(f"Daily Summary - Target: €{format_price(TARGET_PRICE)}")
                last_summary_date = current_date
            
            print(f"\n[{now.strftime('%H:%M:%S')}] Scan #{scan_count + 1}")
            offers = get_offers()
            
            if offers and len(offers) > 0:
                consecutive_failures = 0
                best = offers[0]
                print(f"  Best: €{format_price(best['price'])} (min {format_number(best['quantity'])} Robux)")
                
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
                consecutive_failures += 1
                print(f"  No offers found ({consecutive_failures} failures in a row)")
                
                if consecutive_failures >= 5:
                    send_telegram("⚠️ Warning: Multiple scan failures. The website may be blocking the bot. I'll keep trying.")
                    consecutive_failures = 0
            
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
        running = False
    except Exception as e:
        print(f"\nFATAL: {e}")
        time.sleep(60)
        main()
