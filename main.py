import os
import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, List
from threading import Thread
import time

# Import libraries
from flask import Flask, request, jsonify
from pytrends.request import TrendReq
from telegram import Bot
import pandas as pd

# Setup Flask app
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config từ environment variables - Render sẽ tự động set
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
CHAT_ID = os.getenv('CHAT_ID', 'YOUR_CHAT_ID_HERE')
PORT = int(os.getenv('PORT', 8080))

# Bot settings
CHECK_INTERVAL_MINUTES = 15  # Kiểm tra mỗi 15 phút
SEARCH_THRESHOLD = 1000000   # 1 triệu tìm kiếm
GEO_LOCATION = 'US'          # United States
KEYWORDS_DB_FILE = 'notified_keywords.json'

class NotificationTracker:
    """Theo dõi từ khóa đã thông báo để tránh spam"""
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
                    logger.info(f"Loaded {len(self.notified_4h)} keywords (4h) and {len(self.notified_24h)} keywords (24h)")
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
        
        # Đã thông báo nhưng tăng > 10% -> thông báo lại  
        if keyword in storage and volume > storage[keyword] * 1.1:
            storage[keyword] = volume
            self.save_data()
            return True
        
        return False

class TrendsMonitor:
    """Monitor Google Trends cho US"""
    def __init__(self):
        self.pytrends = TrendReq(hl='en-US', tz=360, geo=GEO_LOCATION)
        self.notification_tracker = NotificationTracker()
        
    def get_trending_keywords(self) -> List[str]:
        """Lấy top trending keywords ở US"""
        try:
            trending_searches = self.pytrends.trending_searches(pn='united_states')
            keywords = trending_searches.tolist()[:30]  # Top 30
            logger.info(f"Got {len(keywords)} trending keywords")
            return keywords
        except Exception as e:
            logger.error(f"Error getting trending keywords: {e}")
            return []
    
    def get_keyword_volume_estimate(self, keyword: str, timeframe: str) -> int:
        """Ước tính volume tìm kiếm"""
        try:
            # Timeframe cho pytrends
            tf = 'now 4-H' if timeframe == '4h' else 'now 1-d'
            
            self.pytrends.build_payload(
                kw_list=[keyword], 
                timeframe=tf,
                geo=GEO_LOCATION
            )
            
            interest_data = self.pytrends.interest_over_time()
            
            if interest_data.empty or keyword not in interest_data.columns:
                return 0
                
            max_interest = interest_data[keyword].max()
            
            # Hệ số ước tính (có thể điều chỉnh)
            if timeframe == '4h':
                estimated_searches = max_interest * 25000  # 4h  
            else:  
                estimated_searches = max_interest * 100000  # 24h
            
            return int(estimated_searches)
            
        except Exception as e:
            logger.error(f"Error getting volume for '{keyword}' ({timeframe}): {e}")
            return 0
    
    def check_keywords(self) -> List[Dict]:
        """Kiểm tra keywords và trả về danh sách cần thông báo"""
        trending_keywords = self.get_trending_keywords()
        notifications = []
        
        for keyword in trending_keywords:
            for timeframe in ['4h', '24h']:
                try:
                    volume = self.get_keyword_volume_estimate(keyword, timeframe)
                    
                    if volume >= SEARCH_THRESHOLD:
                        if self.notification_tracker.should_notify(keyword, volume, timeframe):
                            notifications.append({
                                'keyword': keyword,
                                'volume': volume,
                                'timeframe': timeframe,
                                'timestamp': datetime.now()
                            })
                            logger.info(f"Will notify: {keyword} - {volume:,} searches ({timeframe})")
                    
                    time.sleep(1)  # Avoid Google rate limit
                    
                except Exception as e:
                    logger.error(f"Error checking keyword '{keyword}' ({timeframe}): {e}")
                    continue
        
        return notifications

# Global instances
monitor = TrendsMonitor()
bot_instance = Bot(token=BOT_TOKEN)

# Flask routes
@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy', 
        'timestamp': datetime.now().isoformat(),
        'bot_active': True
    })

@app.route('/')
def home():
    """Home endpoint"""
    return jsonify({
        'message': '🚀 Google Trends Telegram Bot is running!',
        'status': 'active',
        'monitoring': 'US Google Trends',
        'interval': f'{CHECK_INTERVAL_MINUTES} minutes',
        'threshold': f'{SEARCH_THRESHOLD:,} searches'
    })

async def send_notification(keyword_data: Dict):
    """Gửi thông báo Telegram"""
    timeframe_text = "4h qua" if keyword_data['timeframe'] == '4h' else "24h qua"
    
    message = f"""🚨 **CẢNH BÁO** 🚨

🔍 **Từ khóa**: `{keyword_data['keyword']}`
📊 **Đã đạt**: `{keyword_data['volume']:,} lượt tìm kiếm`
⏱️ **Trong**: `{timeframe_text}`
🌍 **Khu vực**: `United States`
📅 **Thời gian**: `{keyword_data['timestamp'].strftime('%H:%M %d/%m/%Y')}`

#TrendAlert #GoogleTrends #USA"""

    try:
        await bot_instance.send_message(
            chat_id=CHAT_ID, 
            text=message, 
            parse_mode='Markdown'
        )
        logger.info(f"✅ Notification sent: {keyword_data['keyword']} ({keyword_data['timeframe']})")
    except Exception as e:
        logger.error(f"❌ Error sending notification: {e}")

def monitoring_loop():
    """Background monitoring task chạy liên tục"""
    logger.info(f"🚀 Starting monitoring every {CHECK_INTERVAL_MINUTES} minutes...")
    
    while True:
        try:
            logger.info("🔍 Checking trends...")
            notifications = monitor.check_keywords()
            
            if notifications:
                # Tạo event loop mới cho async
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                for notification in notifications:
                    loop.run_until_complete(send_notification(notification))
                
                loop.close()
                logger.info(f"✅ Sent {len(notifications)} notifications")
            else:
                logger.info("📊 No keywords exceeded threshold")
            
        except Exception as e:
            logger.error(f"❌ Error in monitoring loop: {e}")
        
        # Chờ 15 phút
        logger.info(f"💤 Waiting {CHECK_INTERVAL_MINUTES} minutes...")
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

# Khởi động monitoring thread
logger.info("🤖 Bot initializing...")
monitor_thread = Thread(target=monitoring_loop, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    logger.info("🚀 Flask app starting...")
    app.run(host='0.0.0.0', port=PORT, debug=False)
