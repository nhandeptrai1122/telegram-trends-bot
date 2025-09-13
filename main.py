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

# Import libraries
from flask import Flask, request, jsonify
from telegram import Bot
import random

# Setup Flask app
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config từ environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
CHAT_ID = os.getenv('CHAT_ID', 'YOUR_CHAT_ID_HERE')
PORT = int(os.getenv('PORT', 8080))

# Bot settings
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
        
        if keyword not in storage and volume >= SEARCH_THRESHOLD:
            storage[keyword] = volume
            self.save_data()
            return True
        
        if keyword in storage and volume > storage[keyword] * 1.1:
            storage[keyword] = volume
            self.save_data()
            return True
        
        return False

class TrendsMonitor:
    """Monitor Google Trends với multiple methods"""
    def __init__(self):
        self.notification_tracker = NotificationTracker()
        self.session = requests.Session()
        
        # Headers để giả lập browser thật
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
    def get_top1_trending_keyword(self) -> str:
        """Lấy TOP 1 trending keyword với multiple fallback methods"""
        
        # Method 1: Scrape Google Trends directly
        try:
            url = "https://trends.google.com/trending?geo=US"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                # Parse HTML để lấy trending keywords
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Tìm trending keywords (có thể thay đổi tùy theo Google)
                trending_elements = soup.find_all('div', {'class': re.compile(r'title|trending|keyword', re.I)})
                
                for element in trending_elements:
                    text = element.get_text().strip()
                    if len(text) > 3 and len(text) < 50:  # Filter reasonable keywords
                        logger.info(f"Found trending keyword via scraping: {text}")
                        return text
                        
        except Exception as e:
            logger.error(f"Method 1 failed: {e}")
        
        # Method 2: Use Google Trends RSS (if available)
        try:
            rss_url = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US"
            response = self.session.get(rss_url, timeout=10)
            
            if response.status_code == 200 and 'xml' in response.headers.get('content-type', ''):
                soup = BeautifulSoup(response.content, 'xml')
                titles = soup.find_all('title')
                
                if len(titles) > 1:  # Skip first title (usually RSS title)
                    keyword = titles[1].get_text().strip()
                    logger.info(f"Found trending keyword via RSS: {keyword}")
                    return keyword
                    
        except Exception as e:
            logger.error(f"Method 2 failed: {e}")
        
        # Method 3: Use popular keywords with rotation
        popular_keywords = [
            'iPhone 16', 'Taylor Swift', 'Trump', 'NFL', 'Weather',
            'Netflix', 'Amazon', 'Google', 'Election 2024', 'Bitcoin',
            'Lakers', 'Instagram', 'YouTube', 'TikTok', 'Spotify',
            'McDonald', 'Starbucks', 'Xbox', 'PlayStation', 'Apple'
        ]
        
        # Rotate keywords để test khác nhau
        current_hour = datetime.now().hour
        selected_keyword = popular_keywords[current_hour % len(popular_keywords)]
        
        logger.info(f"Using fallback keyword: {selected_keyword}")
        return selected_keyword
    
    def get_keyword_volume_estimate(self, keyword: str, timeframe: str) -> int:
        """Ước tính volume với random để test"""
        if not keyword:
            return 0
        
        try:
            # Simulate realistic volume based on keyword popularity
            base_volumes = {
                'iPhone 16': 500000,
                'Taylor Swift': 800000,
                'Trump': 1200000,
                'NFL': 600000,
                'Election 2024': 900000
            }
            
            base_volume = base_volumes.get(keyword, 200000)
            
            # Add randomness để giống thật
            if timeframe == '4h':
                # 4h volume thường thấp hơn
                volume = base_volume + random.randint(-100000, 200000)
            else:  # 24h
                # 24h volume cao hơn
                volume = base_volume * 2 + random.randint(-200000, 500000)
            
            # Ensure không âm
            volume = max(volume, 50000)
            
            logger.info(f"Estimated volume for '{keyword}' ({timeframe}): {volume:,}")
            return volume
            
        except Exception as e:
            logger.error(f"Error estimating volume: {e}")
            return random.randint(80000, 300000)  # Random volume để test
    
    def check_top1_keyword(self) -> List[Dict]:
        """Kiểm tra TOP 1 keyword"""
        top1_keyword = self.get_top1_trending_keyword()
        
        if not top1_keyword:
            return []
        
        notifications = []
        
        for timeframe in ['4h', '24h']:
            try:
                volume = self.get_keyword_volume_estimate(top1_keyword, timeframe)
                logger.info(f"TOP 1 check: '{top1_keyword}' ({timeframe}) = {volume:,} searches")
                
                if volume >= SEARCH_THRESHOLD:
                    if self.notification_tracker.should_notify(top1_keyword, volume, timeframe):
                        notifications.append({
                            'keyword': top1_keyword,
                            'volume': volume,
                            'timeframe': timeframe,
                            'timestamp': datetime.now(),
                            'rank': 'TOP 1'
                        })
                        logger.info(f"🚨 NOTIFICATION: {top1_keyword} - {volume:,} ({timeframe})")
                
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error checking keyword: {e}")
                continue
        
        return notifications

# Global instances
monitor = TrendsMonitor()
bot_instance = Bot(token=BOT_TOKEN)

# Flask routes
@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'bot_active': True,
        'threshold': f'{SEARCH_THRESHOLD:,}',
        'interval': f'{CHECK_INTERVAL_MINUTES} min'
    })

@app.route('/')
def home():
    return jsonify({
        'message': '🥇 Google Trends TOP 1 Monitor (TEST MODE)',
        'threshold': f'{SEARCH_THRESHOLD:,} searches',
        'interval': f'{CHECK_INTERVAL_MINUTES} minutes'
    })

@app.route('/test')
def test_notification():
    """Manual test endpoint"""
    try:
        notifications = monitor.check_top1_keyword()
        return jsonify({
            'notifications_found': len(notifications),
            'notifications': notifications
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

async def send_notification(keyword_data: Dict):
    """Gửi thông báo"""
    timeframe_text = "4h qua" if keyword_data['timeframe'] == '4h' else "24h qua"
    
    message = f"""🥇 **TOP 1 TRENDING ALERT** (TEST) 🥇

🔍 **Từ khóa TOP 1**: `{keyword_data['keyword']}`
📊 **Đã đạt**: `{keyword_data['volume']:,} lượt tìm kiếm`
⏱️ **Trong**: `{timeframe_text}`
🌍 **Khu vực**: `United States`
📅 **Thời gian**: `{keyword_data['timestamp'].strftime('%H:%M %d/%m/%Y')}`
🏆 **Vị trí**: `TOP 1 Trending`

⚠️ **TEST MODE** - Ngưỡng: {SEARCH_THRESHOLD:,}

#TOP1Alert #TestMode #GoogleTrends"""

    try:
        await bot_instance.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode='Markdown'
        )
        logger.info(f"✅ TEST notification sent: {keyword_data['keyword']}")
    except Exception as e:
        logger.error(f"❌ Error sending notification: {e}")

def monitoring_loop():
    """Monitoring loop với better error handling"""
    logger.info(f"🚀 Starting TEST monitoring every {CHECK_INTERVAL_MINUTES} minute(s)...")
    
    while True:
        try:
            logger.info("🔍 Checking TOP 1 keyword...")
            notifications = monitor.check_top1_keyword()
            
            if notifications:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                for notification in notifications:
                    loop.run_until_complete(send_notification(notification))
                
                loop.close()
                logger.info(f"✅ Sent {len(notifications)} notifications")
            else:
                logger.info("📊 No notifications needed")
            
        except Exception as e:
            logger.error(f"❌ Error in monitoring: {e}")
            # Continue instead of crashing
            
        logger.info(f"💤 Waiting {CHECK_INTERVAL_MINUTES} minute(s)...")
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

# Start monitoring
logger.info("🤖 Bot initializing...")
monitor_thread = Thread(target=monitoring_loop, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    logger.info("🚀 Flask app starting...")
    app.run(host='0.0.0.0', port=PORT, debug=False)
