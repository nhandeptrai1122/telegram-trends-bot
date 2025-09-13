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
    
    def clean_old_data(self):
        """Xóa dữ liệu cũ mỗi tuần"""
        current_time = datetime.now()
        if hasattr(self, 'last_clean') and (current_time - self.last_clean).days < 7:
            return
        
        self.notified_4h = {}
        self.notified_24h = {}
        self.save_data()
        self.last_clean = current_time
        logger.info("Cleaned old notification data")

class TrendsMonitor:
    """Monitor Google Trends cho US - chỉ TOP 1"""
    def __init__(self):
        self.pytrends = TrendReq(hl='en-US', tz=360, geo=GEO_LOCATION)
        self.notification_tracker = NotificationTracker()
        
    def get_top1_trending_keyword(self) -> str:
        """Lấy TOP 1 trending keyword từ Google Trends US"""
        try:
            # Phương pháp 1: Lấy trending searches
            trending_searches = self.pytrends.trending_searches(pn='united_states')
            
            if not trending_searches.empty:
                top1_keyword = trending_searches.iloc[0, 0]  # TOP 1
                logger.info(f"TOP 1 trending: {top1_keyword}")
                return str(top1_keyword)
                
        except Exception as e:
            logger.error(f"Error getting trending searches: {e}")
        
        try:
            # Phương pháp 2: Fallback với realtime data
            self.pytrends.build_payload(kw_list=[''], timeframe='now 4-H', geo=GEO_LOCATION)
            
            # Danh sách keywords phổ biến để test
            popular_keywords = [
                'iPhone 16', 'Taylor Swift', 'Trump', 'NFL', 'Weather',
                'Netflix', 'Amazon', 'Google', 'Election 2024', 'Bitcoin'
            ]
            
            # Tìm keyword có interest cao nhất
            max_interest = 0
            top_keyword = popular_keywords[0]
            
            for keyword in popular_keywords[:5]:  # Test top 5
                try:
                    self.pytrends.build_payload([keyword], timeframe='now 4-H', geo=GEO_LOCATION)
                    interest_data = self.pytrends.interest_over_time()
                    
                    if not interest_data.empty and keyword in interest_data.columns:
                        current_interest = interest_data[keyword].max()
                        if current_interest > max_interest:
                            max_interest = current_interest
                            top_keyword = keyword
                    
                    time.sleep(1)  # Rate limiting
                except:
                    continue
            
            logger.info(f"Fallback TOP 1: {top_keyword} (interest: {max_interest})")
            return top_keyword
            
        except Exception as e:
            logger.error(f"Error in fallback method: {e}")
            return "iPhone 16"  # Final fallback
    
    def get_keyword_volume_estimate(self, keyword: str, timeframe: str) -> int:
        """Ước tính số lượng tìm kiếm cho keyword TOP 1"""
        if not keyword:
            return 0
            
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
                logger.warning(f"No data for keyword: {keyword}")
                return 0
                
            max_interest = interest_data[keyword].max()
            
            # Hệ số ước tính cho TOP 1 trending (thường cao hơn)
            if timeframe == '4h':
                # TOP 1 trending trong 4h có thể có volume rất cao
                estimated_searches = max_interest * 50000  # Tăng hệ số cho TOP 1
            else:  # 24h
                # TOP 1 trending trong 24h
                estimated_searches = max_interest * 200000  # Tăng hệ số cho TOP 1
            
            logger.info(f"TOP 1 '{keyword}' ({timeframe}): interest={max_interest}, estimated={estimated_searches:,}")
            return int(estimated_searches)
            
        except Exception as e:
            logger.error(f"Error getting volume for TOP 1 '{keyword}' ({timeframe}): {e}")
            return 0
    
    def check_top1_keyword(self) -> List[Dict]:
        """Kiểm tra TOP 1 keyword có volume cao và cần thông báo"""
        # Lấy TOP 1 trending keyword
        top1_keyword = self.get_top1_trending_keyword()
        
        if not top1_keyword:
            logger.warning("No TOP 1 keyword found")
            return []
        
        notifications = []
        
        # Kiểm tra cho cả 4h và 24h
        for timeframe in ['4h', '24h']:
            try:
                volume = self.get_keyword_volume_estimate(top1_keyword, timeframe)
                
                logger.info(f"TOP 1 check: '{top1_keyword}' ({timeframe}) = {volume:,} searches")
                
                if volume >= SEARCH_THRESHOLD:
                    # Kiểm tra có nên thông báo không
                    if self.notification_tracker.should_notify(top1_keyword, volume, timeframe):
                        notifications.append({
                            'keyword': top1_keyword,
                            'volume': volume,
                            'timeframe': timeframe,
                            'timestamp': datetime.now(),
                            'rank': 'TOP 1'  # Đánh dấu là TOP 1
                        })
                        logger.info(f"🚨 TOP 1 ALERT: {top1_keyword} - {volume:,} searches ({timeframe})")
                
                time.sleep(2)  # Rate limiting cho Google
                
            except Exception as e:
                logger.error(f"Error checking TOP 1 keyword '{top1_keyword}' ({timeframe}): {e}")
                continue
        
        # Clean old data định kỳ
        self.notification_tracker.clean_old_data()
        
        return notifications

# Global instances
monitor = TrendsMonitor()
bot_instance = Bot(token=BOT_TOKEN)

# Flask routes
@app.route('/health')
def health():
    """Health check endpoint cho UptimeRobot"""
    return jsonify({
        'status': 'healthy', 
        'timestamp': datetime.now().isoformat(),
        'bot_active': True,
        'monitoring': 'TOP 1 Google Trends US',
        'threshold': f'{SEARCH_THRESHOLD:,} searches'
    })

@app.route('/')
def home():
    """Home endpoint"""
    return jsonify({
        'message': '🥇 Google Trends TOP 1 Monitor Bot is running!',
        'status': 'active',
        'monitoring': 'TOP 1 trending keyword in US',
        'interval': f'{CHECK_INTERVAL_MINUTES} minutes',
        'threshold': f'{SEARCH_THRESHOLD:,} searches',
        'timeframes': ['4 hours', '24 hours']
    })

@app.route('/status')
def status():
    """Status endpoint để kiểm tra bot"""
    try:
        current_top1 = monitor.get_top1_trending_keyword()
        return jsonify({
            'bot_status': 'running',
            'current_top1': current_top1,
            'last_check': datetime.now().isoformat(),
            'notifications_sent_4h': len(monitor.notification_tracker.notified_4h),
            'notifications_sent_24h': len(monitor.notification_tracker.notified_24h)
        })
    except Exception as e:
        return jsonify({
            'bot_status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

async def send_notification(keyword_data: Dict):
    """Gửi thông báo TOP 1 trending"""
    timeframe_text = "4h qua" if keyword_data['timeframe'] == '4h' else "24h qua"
    
    message = f"""🥇 **TOP 1 TRENDING ALERT** 🥇

🔍 **Từ khóa TOP 1**: `{keyword_data['keyword']}`
📊 **Đã đạt**: `{keyword_data['volume']:,} lượt tìm kiếm`
⏱️ **Trong**: `{timeframe_text}`
🌍 **Khu vực**: `United States`
📅 **Thời gian**: `{keyword_data['timestamp'].strftime('%H:%M %d/%m/%Y')}`
🏆 **Vị trí**: `TOP 1 Trending`

#TOP1Alert #GoogleTrends #USA"""

    try:
        await bot_instance.send_message(
            chat_id=CHAT_ID, 
            text=message, 
            parse_mode='Markdown'
        )
        logger.info(f"✅ TOP 1 notification sent: {keyword_data['keyword']} ({keyword_data['timeframe']})")
    except Exception as e:
        logger.error(f"❌ Error sending TOP 1 notification: {e}")

def monitoring_loop():
    """Background monitoring task chỉ theo dõi TOP 1"""
    logger.info(f"🚀 Starting TOP 1 monitoring every {CHECK_INTERVAL_MINUTES} minutes...")
    
    while True:
        try:
            logger.info("🔍 Checking TOP 1 trending keyword...")
            notifications = monitor.check_top1_keyword()  # Chỉ check TOP 1
            
            if notifications:
                # Tạo event loop mới cho async
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                for notification in notifications:
                    loop.run_until_complete(send_notification(notification))
                
                loop.close()
                logger.info(f"✅ Sent {len(notifications)} TOP 1 notifications")
            else:
                logger.info("📊 TOP 1 keyword has not exceeded threshold")
            
        except Exception as e:
            logger.error(f"❌ Error in TOP 1 monitoring loop: {e}")
        
        # Chờ 15 phút
        logger.info(f"💤 Waiting {CHECK_INTERVAL_MINUTES} minutes for next TOP 1 check...")
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

# Khởi động monitoring thread
logger.info("🤖 TOP 1 Trends Bot initializing...")
monitor_thread = Thread(target=monitoring_loop, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    logger.info("🚀 Flask app starting...")
    app.run(host='0.0.0.0', port=PORT, debug=False)
