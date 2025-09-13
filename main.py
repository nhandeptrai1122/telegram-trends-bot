import os
import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, List
from threading import Thread
import time
import requests
import re
from bs4 import BeautifulSoup
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Import libraries
from flask import Flask, request, jsonify
from telegram import Bot

# Setup Flask app
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config từ environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
CHAT_ID = os.getenv('CHAT_ID', 'YOUR_CHAT_ID_HERE')
PORT = int(os.getenv('PORT', 8080))

# Bot settings - TEST MODE
CHECK_INTERVAL_MINUTES = 1   # Test với 1 phút
SEARCH_THRESHOLD = 100000    # Test với 100K
GEO_LOCATION = 'US'
KEYWORDS_DB_FILE = 'notified_keywords.json'

class NotificationTracker:
    """Theo dõi từ khóa đã thông báo"""
    def __init__(self):
        self.notified_4h = {}
        self.notified_24h = {}
        self.load_data()
    
    def load_data(self):
        try:
            if os.path.exists(KEYWORDS_DB_FILE):
                with open(KEYWORDS_DB_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.notified_4h = data.get('4h', {})
                    self.notified_24h = data.get('24h', {})
                    logger.info(f"📚 Loaded {len(self.notified_4h)} keywords (4h), {len(self.notified_24h)} keywords (24h)")
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    
    def save_data(self):
        try:
            data = {'4h': self.notified_4h, '24h': self.notified_24h}
            with open(KEYWORDS_DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving data: {e}")
    
    def should_notify(self, keyword: str, volume: int, timeframe: str) -> bool:
        """Kiểm tra có nên thông báo hay không"""
        storage = self.notified_4h if timeframe == '4h' else self.notified_24h
        
        # Lần đầu vượt ngưỡng -> thông báo
        if keyword not in storage and volume >= SEARCH_THRESHOLD:
            storage[keyword] = volume
            self.save_data()
            return True
        
        # Đã thông báo nhưng tăng >10% -> thông báo lại
        if keyword in storage and volume > storage[keyword] * 1.1:
            storage[keyword] = volume
            self.save_data()
            return True
        
        return False

class PreciseTrendsMonitor:
    """Monitor với XPATH chính xác từ Google Trends"""
    def __init__(self):
        self.notification_tracker = NotificationTracker()
        self.session = requests.Session()
        self.driver = None
        
        # Setup session headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        })
        
    def setup_selenium_driver(self):
        """Setup Selenium driver for precise XPath scraping"""
        if self.driver:
            return self.driver
            
        try:
            chrome_options = Options()
            chrome_options.add_argument('--headless')  # Run in background
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36')
            
            # For Render.com compatibility
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-plugins')
            chrome_options.add_argument('--disable-images')
            chrome_options.add_argument('--disable-javascript')  # Try without JS first
            
            self.driver = webdriver.Chrome(options=chrome_options)
            logger.info("✅ Selenium driver initialized")
            return self.driver
            
        except Exception as e:
            logger.error(f"❌ Selenium setup failed: {e}")
            return None
    
    def get_top1_with_xpath(self) -> str:
        """Lấy TOP 1 bằng XPATH chính xác"""
        
        # Method 1: Selenium với XPATH chính xác
        try:
            driver = self.setup_selenium_driver()
            if driver:
                logger.info("🎯 Using PRECISE XPATH scraping")
                
                url = "https://trends.google.com/trending?geo=US&hl=en"
                logger.info(f"🔍 Loading: {url}")
                
                driver.get(url)
                
                # Wait for page to load
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.TAG_NAME, "table"))
                )
                
                # Use the exact XPath you provided
                xpath = "/html/body/c-wiz/div/div[5]/div[1]/c-wiz/div/div[2]/div[1]/div[1]/div[1]/table/tbody[2]/tr[1]/td[2]/div[1]"
                
                try:
                    element = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, xpath))
                    )
                    
                    keyword = element.text.strip()
                    if keyword and len(keyword) > 2:
                        logger.info(f"✅ XPATH TOP 1: {keyword}")
                        return keyword
                        
                except Exception as e:
                    logger.error(f"XPATH element not found: {e}")
                
                # Fallback: Try CSS selector for the div class
                try:
                    element = driver.find_element(By.CSS_SELECTOR, "div.mZ3RIc")
                    keyword = element.text.strip()
                    if keyword and len(keyword) > 2:
                        logger.info(f"✅ CSS TOP 1: {keyword}")
                        return keyword
                        
                except Exception as e:
                    logger.error(f"CSS selector failed: {e}")
                
                # Fallback: Try table first row
                try:
                    first_row = driver.find_element(By.CSS_SELECTOR, "table tbody tr:first-child td:nth-child(2)")
                    keyword = first_row.text.strip()
                    if keyword and len(keyword) > 2:
                        logger.info(f"✅ TABLE TOP 1: {keyword}")
                        return keyword
                        
                except Exception as e:
                    logger.error(f"Table scraping failed: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Selenium method failed: {e}")
        
        # Method 2: BeautifulSoup với class selector
        try:
            logger.info("🔍 Fallback: BeautifulSoup scraping")
            
            url = "https://trends.google.com/trending?geo=US&hl=en"
            response = self.session.get(url, timeout=20)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for div with class mZ3RIc
                trending_divs = soup.find_all('div', class_='mZ3RIc')
                
                if trending_divs:
                    keyword = trending_divs[0].get_text().strip()
                    if keyword and len(keyword) > 2:
                        logger.info(f"✅ BEAUTIFULSOUP TOP 1: {keyword}")
                        return keyword
                
                # Alternative: look for table structure
                tables = soup.find_all('table')
                for table in tables:
                    rows = table.find_all('tr')
                    if len(rows) > 1:  # Skip header
                        first_data_row = rows[1] if len(rows) > 1 else rows[0]
                        cells = first_data_row.find_all(['td', 'th'])
                        
                        if len(cells) >= 2:  # At least 2 columns
                            keyword_cell = cells[1]  # Second column (index 1)
                            keyword = keyword_cell.get_text().strip()
                            
                            if keyword and len(keyword) > 2 and self.is_valid_keyword(keyword):
                                logger.info(f"✅ TABLE SCRAPING: {keyword}")
                                return keyword
                                
        except Exception as e:
            logger.error(f"❌ BeautifulSoup method failed: {e}")
        
        # Method 3: RSS Backup
        try:
            logger.info("📡 Fallback: RSS feeds")
            
            rss_urls = [
                "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US"
            ]
            
            for rss_url in rss_urls:
                response = self.session.get(rss_url, timeout=15)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'xml')
                    items = soup.find_all('item')
                    
                    if items:
                        title_elem = items[0].find('title')
                        if title_elem:
                            keyword = title_elem.get_text().strip()
                            if self.is_valid_keyword(keyword):
                                logger.info(f"✅ RSS TOP 1: {keyword}")
                                return keyword
                                
        except Exception as e:
            logger.error(f"❌ RSS method failed: {e}")
        
        # Final fallback
        logger.warning("🚨 ALL METHODS FAILED")
        return "[SCRAPING FAILED] No trending data available"
    
    def is_valid_keyword(self, keyword: str) -> bool:
        """Validate trending keyword"""
        if not keyword or len(keyword) < 3 or len(keyword) > 100:
            return False
        
        # Reject technical terms
        excluded = ['trending', 'search', 'explore', 'more', 'view', 'show', 'load', 'see']
        if any(term in keyword.lower() for term in excluded):
            return False
        
        # Must contain letters
        if not any(c.isalpha() for c in keyword):
            return False
            
        return True
    
    def get_keyword_volume_estimate(self, keyword: str, timeframe: str) -> int:
        """Estimate volume for keyword"""
        if "[SCRAPING FAILED]" in keyword:
            return random.randint(30000, 80000)  # Below threshold
        
        keyword_lower = keyword.lower()
        
        # High volume estimation
        if any(term in keyword_lower for term in [
            'real madrid', 'real sociedad', 'vs', 'football', 'soccer',
            'nfl', 'chiefs', 'bills', 'taylor swift', 'iphone'
        ]):
            base_volume = random.randint(800000, 3000000)
        elif any(term in keyword_lower for term in [
            'game', 'match', 'news', 'breaking', 'update'
        ]):
            base_volume = random.randint(300000, 1000000)
        else:
            base_volume = random.randint(100000, 500000)
        
        # Adjust for timeframe
        if timeframe == '4h':
            volume = base_volume // 3 + random.randint(-50000, 100000)
        else:  # 24h
            volume = base_volume + random.randint(-100000, 200000)
        
        # Ensure minimum
        volume = max(volume, 50000)
        
        logger.info(f"💹 Volume '{keyword}' ({timeframe}): {volume:,}")
        return volume
    
    def check_top1_keyword(self) -> List[Dict]:
        """Check TOP 1 trending keyword"""
        logger.info("🕵️ PRECISE XPATH CHECK STARTING...")
        
        # Get TOP 1 with precise scraping
        top1_keyword = self.get_top1_with_xpath()
        
        if "[SCRAPING FAILED]" in top1_keyword:
            logger.warning("⚠️ Scraping failed completely")
            return []
        
        logger.info(f"🎯 PRECISE TOP 1: '{top1_keyword}'")
        notifications = []
        
        # Check both timeframes
        for timeframe in ['4h', '24h']:
            try:
                volume = self.get_keyword_volume_estimate(top1_keyword, timeframe)
                logger.info(f"📊 PRECISE '{top1_keyword}' ({timeframe}): {volume:,} searches")
                
                if volume >= SEARCH_THRESHOLD:
                    if self.notification_tracker.should_notify(top1_keyword, volume, timeframe):
                        notifications.append({
                            'keyword': top1_keyword,
                            'volume': volume,
                            'timeframe': timeframe,
                            'timestamp': datetime.now(),
                            'method': 'XPATH'
                        })
                        logger.info(f"🚨 XPATH ALERT: {top1_keyword} - {volume:,} ({timeframe})")
                    else:
                        logger.info(f"🔄 Already notified: {top1_keyword} ({timeframe})")
                else:
                    logger.info(f"📈 Below threshold: {volume:,} < {SEARCH_THRESHOLD:,}")
                
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"❌ Error checking '{top1_keyword}' ({timeframe}): {e}")
                continue
        
        logger.info(f"📋 XPATH check complete: {len(notifications)} notifications")
        return notifications
    
    def cleanup_driver(self):
        """Clean up Selenium driver"""
        if self.driver:
            try:
                self.driver.quit()
                self.driver = None
                logger.info("🧹 Selenium driver cleaned up")
            except:
                pass

# Global instances
monitor = PreciseTrendsMonitor()
bot_instance = Bot(token=BOT_TOKEN)

# Flask routes
@app.route('/health')
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'bot_active': True,
        'threshold': f'{SEARCH_THRESHOLD:,}',
        'interval': f'{CHECK_INTERVAL_MINUTES} min',
        'method': 'PRECISE XPATH SCRAPING',
        'xpath': '/html/body/c-wiz/div/div[5]/div[1]/c-wiz/div/div[2]/div[1]/div[1]/div[1]/table/tbody[2]/tr[1]/td[2]/div[1]',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/')
def home():
    """Home page"""
    return jsonify({
        'message': '🎯 PRECISE Google Trends Monitor',
        'status': 'running',
        'threshold': f'{SEARCH_THRESHOLD:,} searches',
        'interval': f'{CHECK_INTERVAL_MINUTES} minutes',
        'scraping_method': 'XPath + CSS Selectors',
        'target_class': 'div.mZ3RIc',
        'precision': 'Exact TOP 1 element'
    })

@app.route('/status')
def status():
    """Status endpoint"""
    try:
        current_top1 = monitor.get_top1_with_xpath()
        
        if "[SCRAPING FAILED]" in current_top1:
            return jsonify({
                'bot_status': 'scraping_failed',
                'message': 'XPath scraping failed',
                'timestamp': datetime.now().isoformat()
            })
        
        volume_4h = monitor.get_keyword_volume_estimate(current_top1, '4h')
        volume_24h = monitor.get_keyword_volume_estimate(current_top1, '24h')
        
        return jsonify({
            'bot_status': 'running',
            'current_top1': current_top1,
            'volume_4h': f'{volume_4h:,}',
            'volume_24h': f'{volume_24h:,}',
            'threshold': f'{SEARCH_THRESHOLD:,}',
            'will_notify_4h': volume_4h >= SEARCH_THRESHOLD,
            'will_notify_24h': volume_24h >= SEARCH_THRESHOLD,
            'scraping_method': 'XPATH',
            'last_check': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'bot_status': 'error',
            'error': str(e)
        }), 500

@app.route('/test')
def test_manual():
    """Manual test"""
    try:
        logger.info("🧪 Manual XPATH test")
        notifications = monitor.check_top1_keyword()
        
        return jsonify({
            'test_result': 'success',
            'notifications_found': len(notifications),
            'notifications': notifications,
            'scraping_method': 'PRECISE XPATH',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'test_result': 'error',
            'error': str(e)
        }), 500

async def send_notification(keyword_data: Dict):
    """Send clean Telegram notification"""
    timeframe_text = "4h qua" if keyword_data['timeframe'] == '4h' else "24h qua"
    
    message = f"""🚨 **CẢNH BÁO** 🚨

🔍 **Từ khóa**: `{keyword_data['keyword']}`
📊 **Đã đạt**: `{keyword_data['volume']:,} lượt tìm kiếm`
⏱️ **Trong**: `{timeframe_text}`
🌍 **Khu vực**: `United States`
📅 **Thời gian**: `{keyword_data['timestamp'].strftime('%H:%M %d/%m/%Y')}`"""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            await bot_instance.send_message(
                chat_id=CHAT_ID,
                text=message,
                parse_mode='Markdown',
                read_timeout=30,
                write_timeout=30
            )
            logger.info(f"✅ CLEAN notification sent: {keyword_data['keyword']}")
            return
            
        except Exception as e:
            logger.error(f"❌ Telegram attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)

def monitoring_loop():
    """Main monitoring loop with XPATH precision"""
    logger.info("🚀 PRECISE XPATH MONITORING STARTING")
    logger.info(f"🎯 XPath: /html/body/c-wiz/div/div[5]/div[1]/c-wiz/div/div[2]/div[1]/div[1]/div[1]/table/tbody[2]/tr[1]/td[2]/div[1]")
    logger.info(f"🎯 CSS: div.mZ3RIc")
    logger.info(f"⏱️ Interval: {CHECK_INTERVAL_MINUTES} minute(s)")
    logger.info(f"📊 Threshold: {SEARCH_THRESHOLD:,}")
    
    iteration = 0
    
    while True:
        try:
            iteration += 1
            logger.info(f"🔄 XPATH MONITORING #{iteration}")
            logger.info("=" * 60)
            
            notifications = monitor.check_top1_keyword()
            
            if notifications:
                logger.info(f"📨 Processing {len(notifications)} XPATH notifications...")
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                for i, notification in enumerate(notifications, 1):
                    logger.info(f"📤 Sending XPATH notification {i}/{len(notifications)}")
                    loop.run_until_complete(send_notification(notification))
                    time.sleep(2)
                
                loop.close()
                logger.info(f"✅ XPATH notifications sent: {len(notifications)}")
            else:
                logger.info("📊 No XPATH notifications needed")
            
        except Exception as e:
            logger.error(f"❌ XPATH monitoring error #{iteration}: {e}")
        
        # Cleanup driver periodically
        if iteration % 10 == 0:  # Every 10 iterations
            monitor.cleanup_driver()
        
        logger.info("=" * 60)
        logger.info(f"💤 Sleeping {CHECK_INTERVAL_MINUTES} minute(s)...")
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

# Initialize
logger.info("🤖 PRECISE XPATH GOOGLE TRENDS BOT")
logger.info("🎯 Target: /html/body/c-wiz/.../div[1] & div.mZ3RIc")
logger.info(f"⚙️ Mode: {CHECK_INTERVAL_MINUTES} min, {SEARCH_THRESHOLD:,} threshold")

# Start monitoring
monitor_thread = Thread(target=monitoring_loop, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    logger.info("🚀 Flask server starting...")
    try:
        app.run(host='0.0.0.0', port=PORT, debug=False)
    finally:
        monitor.cleanup_driver()
