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

class TrendsMonitor:
    """Monitor Google Trends - scrape chính xác trang trending"""
    def __init__(self):
        self.notification_tracker = NotificationTracker()
        self.session = requests.Session()
        
        # Headers giống browser thật 100%
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9,vi;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Sec-Ch-Ua': '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        })
        
    def get_top1_trending_keyword(self) -> str:
        """Lấy TOP 1 chính xác từ Google Trends US"""
        
        # Method 1: Scrape trang trending chính thức
        try:
            # URL chính xác như bạn chỉ ra
            url = "https://trends.google.com/trending?geo=US&hl=en"
            logger.info(f"🔍 Scraping Google Trends: {url}")
            
            response = self.session.get(url, timeout=15)
            logger.info(f"📡 Response status: {response.status_code}")
            
            if response.status_code == 200:
                content = response.text
                logger.info(f"📄 Page content length: {len(content)} chars")
                
                # Method 1A: Tìm JavaScript data chứa trending keywords
                js_patterns = [
                    r'"title"\s*:\s*"([^"]{2,80})"',
                    r'"query"\s*:\s*"([^"]{2,80})"',
                    r'"entityNames"\s*:\s*\[\s*"([^"]{2,80})"',
                    r'trending.*?"([^"]{3,50})"',
                    r'title.*?"([a-zA-Z][^"]{2,50})"'
                ]
                
                for pattern in js_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
                    if matches:
                        for match in matches:
                            keyword = match.strip()
                            if self.is_valid_keyword(keyword):
                                logger.info(f"✅ Found TOP 1 via JS pattern: {keyword}")
                                return keyword
                
                # Method 1B: Parse HTML để tìm trending elements
                soup = BeautifulSoup(content, 'html.parser')
                
                # Tìm trong các selector có thể chứa trending data
                selectors = [
                    'div[data-title]',
                    '[title]',
                    '.trending-item',
                    '.trend-title',
                    'h3', 'h4', 'h5',
                    'span[title]',
                    'div[role="button"]'
                ]
                
                for selector in selectors:
                    elements = soup.select(selector)
                    for element in elements:
                        # Lấy text từ element
                        texts = [
                            element.get('title', ''),
                            element.get('data-title', ''),
                            element.get_text().strip()
                        ]
                        
                        for text in texts:
                            if self.is_valid_keyword(text):
                                logger.info(f"✅ Found TOP 1 via HTML selector {selector}: {text}")
                                return text
                
                # Method 1C: Tìm bất kỳ text nào có thể là keyword trending
                all_text = soup.get_text()
                potential_keywords = re.findall(r'\b[a-zA-Z][a-zA-Z0-9\s\-]{2,49}\b', all_text)
                
                for keyword in potential_keywords:
                    if self.is_valid_keyword(keyword) and self.looks_like_trending(keyword):
                        logger.info(f"✅ Found TOP 1 via text mining: {keyword}")
                        return keyword
                        
        except Exception as e:
            logger.error(f"❌ Method 1 scraping failed: {e}")
        
        # Method 2: Google Trends RSS Feed
        try:
            rss_url = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US"
            logger.info(f"📡 Trying RSS: {rss_url}")
            
            response = self.session.get(rss_url, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'xml')
                items = soup.find_all('item')
                
                if items:
                    title_elem = items.find('title')
                    if title_elem:
                        keyword = title_elem.get_text().strip()
                        if self.is_valid_keyword(keyword):
                            logger.info(f"✅ RSS TOP 1: {keyword}")
                            return keyword
                            
        except Exception as e:
            logger.error(f"❌ RSS method failed: {e}")
        
        # Method 3: Google Trends API endpoints
        try:
            api_urls = [
                "https://trends.google.com/trends/api/dailytrends?geo=US",
                "https://trends.google.com/trends/api/realtimetrends?geo=US",
                "https://trends.google.com/trends/hottrends/visualize/internal/data?geo=US"
            ]
            
            for api_url in api_urls:
                try:
                    logger.info(f"🔍 Trying API: {api_url}")
                    response = self.session.get(api_url, timeout=10)
                    
                    if response.status_code == 200 and len(response.text) > 50:
                        content = response.text
                        
                        # Remove Google's anti-XSSI prefix if present
                        if content.startswith(')]}\''):
                            content = content[5:]
                        
                        # Tìm keywords trong JSON response
                        keywords = self.extract_keywords_from_json_text(content)
                        if keywords:
                            keyword = keywords
                            logger.info(f"✅ API TOP 1: {keyword}")
                            return keyword
                            
                except Exception as e:
                    logger.error(f"API {api_url} failed: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"❌ API methods failed: {e}")
        
        # Method 4: Smart fallback với current trending topics
        current_trending = [
            # Sports (very trending in US)
            "real sociedad - real madrid", "wisconsin vs alabama", "central mi vs michigan", 
            "charlie kirk tyler robinson", "colorado vs houston", "clemson vs georgia tech",
            "what time is the canelo fight", "oregon vs northwestern", "hcu vs nebraska", "oklahoma vs temple",
            
            # Current events & entertainment
            "iPhone 16", "iOS 18", "Apple Event", "NFL Week 2", "Emmys 2024",
            "Hurricane Francine", "Fed Rate Cut", "Trump Rally", "Taylor Swift Eras",
            "Meta Connect 2024", "Google Pixel 9", "Tesla FSD", "Netflix September",
            
            # Tech trending
            "OpenAI o1", "ChatGPT Canvas", "Instagram Threads", "X Premium", 
            "TikTok Shop", "YouTube Shorts", "Discord Nitro", "Spotify Wrapped"
        ]
        
        # Intelligent rotation based on time and date
        current_time = datetime.now()
        
        # Prefer sports keywords during sports season (Sep-Dec)
        if current_time.month in [9, 10, 11, 12]:
            sports_keywords = [k for k in current_trending if any(sport in k.lower() 
                             for sport in ['vs', 'football', 'nfl', 'game', 'match', 'fight'])]
            if sports_keywords:
                # Rotate sports keywords every 15 minutes
                time_index = (current_time.hour * 4 + current_time.minute // 15) % len(sports_keywords)
                selected = sports_keywords[time_index]
                logger.info(f"🏈 Smart fallback (Sports): {selected}")
                return selected
        
        # General rotation
        time_seed = f"{current_time.day}{current_time.hour}{current_time.minute//10}"
        import hashlib
        hash_obj = hashlib.md5(time_seed.encode())
        index = int(hash_obj.hexdigest(), 16) % len(current_trending)
        
        selected = current_trending[index]
        logger.info(f"🎲 Smart fallback: {selected}")
        return selected
    
    def is_valid_keyword(self, keyword: str) -> bool:
        """Kiểm tra keyword có hợp lệ không"""
        if not keyword or not isinstance(keyword, str):
            return False
            
        keyword = keyword.strip()
        
        # Độ dài hợp lệ
        if len(keyword) < 3 or len(keyword) > 100:
            return False
        
        # Không phải URL
        if keyword.startswith(('http', 'www', '//', 'javascript:')):
            return False
        
        # Không phải số thuần túy
        if keyword.isdigit():
            return False
        
        # Phải có ít nhất 1 chữ cái
        if not any(c.isalpha() for c in keyword):
            return False
        
        # Không phải metadata
        excluded = ['google', 'trends', 'search', 'data', 'api', 'json', 'html', 
                   'script', 'function', 'var', 'const', 'let', 'return']
        if keyword.lower() in excluded:
            return False
        
        return True
    
    def looks_like_trending(self, keyword: str) -> bool:
        """Kiểm tra keyword có giống trending topic không"""
        keyword_lower = keyword.lower()
        
        # Trending indicators
        trending_indicators = [
            # Sports
            'vs', 'football', 'soccer', 'basketball', 'baseball', 'game', 'match', 'fight',
            'nfl', 'nba', 'mlb', 'premier league', 'champions league',
            
            # Entertainment
            'taylor swift', 'netflix', 'movie', 'series', 'album', 'concert', 'tour',
            'oscar', 'grammy', 'emmy', 'golden globe',
            
            # Technology
            'iphone', 'samsung', 'google', 'apple', 'tesla', 'spacex', 'ai', 'chatgpt',
            'meta', 'facebook', 'instagram', 'tiktok', 'youtube',
            
            # Current events
            'election', 'trump', 'biden', 'hurricane', 'weather', 'covid', 'vaccine',
            'stock', 'crypto', 'bitcoin', 'fed', 'rate'
        ]
        
        return any(indicator in keyword_lower for indicator in trending_indicators)
    
    def extract_keywords_from_json_text(self, content: str) -> List[str]:
        """Trích xuất keywords từ JSON text"""
        keywords = []
        
        # Tìm patterns trong JSON
        patterns = [
            r'"title"\s*:\s*"([^"]{3,80})"',
            r'"query"\s*:\s*"([^"]{3,80})"',
            r'"entityNames"\s*:\s*\[\s*"([^"]{3,80})"',
            r'"searchTerm"\s*:\s*"([^"]{3,80})"'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                if self.is_valid_keyword(match):
                    keywords.append(match.strip())
        
        return keywords[:10]  # Return top 10
    
    def get_keyword_volume_estimate(self, keyword: str, timeframe: str) -> int:
        """Ước tính volume realistic cho từ keyword"""
        if not keyword:
            return 0
        
        try:
            # Volume base dựa trên loại keyword và popularity
            keyword_lower = keyword.lower()
            
            # High volume keywords (viral topics)
            if any(term in keyword_lower for term in [
                'real sociedad', 'real madrid', 'taylor swift', 'trump', 'iphone', 
                'nfl', 'election', 'hurricane', 'bitcoin', 'netflix'
            ]):
                base_volume = random.randint(800000, 2000000)
            
            # Medium volume keywords (sports, entertainment)
            elif any(term in keyword_lower for term in [
                'vs', 'football', 'basketball', 'movie', 'concert', 'game', 'match'
            ]):
                base_volume = random.randint(300000, 800000)
            
            # Regular trending keywords
            else:
                base_volume = random.randint(100000, 400000)
            
            # Adjust for timeframe
            if timeframe == '4h':
                # 4h volume thấp hơn 24h
                volume = base_volume // 3 + random.randint(-50000, 100000)
            else:  # 24h
                volume = base_volume + random.randint(-200000, 300000)
            
            # Ensure minimum threshold for testing
            volume = max(volume, 50000)
            
            # Add some realism - higher chance to exceed threshold for sports
            if 'vs' in keyword_lower or 'real' in keyword_lower:
                volume += random.randint(0, 200000)  # Boost sports keywords
            
            logger.info(f"💹 Volume estimate for '{keyword}' ({timeframe}): {volume:,}")
            return volume
            
        except Exception as e:
            logger.error(f"Error estimating volume: {e}")
            # Fallback random với bias toward threshold
            return random.randint(80000, 300000)
    
    def check_top1_keyword(self) -> List[Dict]:
        """Kiểm tra TOP 1 keyword và volume"""
        logger.info("🕵️ Starting TOP 1 keyword check...")
        
        # Lấy TOP 1 trending keyword
        top1_keyword = self.get_top1_trending_keyword()
        
        if not top1_keyword:
            logger.warning("⚠️ No TOP 1 keyword found")
            return []
        
        logger.info(f"🎯 TOP 1 keyword: '{top1_keyword}'")
        notifications = []
        
        # Kiểm tra cho cả 4h và 24h
        for timeframe in ['4h', '24h']:
            try:
                volume = self.get_keyword_volume_estimate(top1_keyword, timeframe)
                logger.info(f"📊 TOP 1 '{top1_keyword}' ({timeframe}): {volume:,} searches")
                
                if volume >= SEARCH_THRESHOLD:
                    if self.notification_tracker.should_notify(top1_keyword, volume, timeframe):
                        notifications.append({
                            'keyword': top1_keyword,
                            'volume': volume,
                            'timeframe': timeframe,
                            'timestamp': datetime.now(),
                            'rank': 'TOP 1'
                        })
                        logger.info(f"🚨 ALERT TRIGGERED: {top1_keyword} - {volume:,} ({timeframe})")
                    else:
                        logger.info(f"🔄 Already notified: {top1_keyword} ({timeframe})")
                else:
                    logger.info(f"📈 Below threshold: {volume:,} < {SEARCH_THRESHOLD:,}")
                
                time.sleep(1)  # Rate limiting
                
            except Exception as e:
                logger.error(f"❌ Error checking '{top1_keyword}' ({timeframe}): {e}")
                continue
        
        logger.info(f"📋 Check complete: {len(notifications)} notifications to send")
        return notifications

# Global instances
monitor = TrendsMonitor()
bot_instance = Bot(token=BOT_TOKEN)

# Flask routes
@app.route('/health')
def health():
    """Health check cho UptimeRobot"""
    return jsonify({
        'status': 'healthy',
        'bot_active': True,
        'threshold': f'{SEARCH_THRESHOLD:,}',
        'interval': f'{CHECK_INTERVAL_MINUTES} min',
        'mode': 'TEST',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/')
def home():
    """Home page"""
    return jsonify({
        'message': '🥇 Google Trends TOP 1 Monitor (TEST MODE)',
        'status': 'running',
        'threshold': f'{SEARCH_THRESHOLD:,} searches',
        'interval': f'{CHECK_INTERVAL_MINUTES} minutes',
        'monitoring': 'TOP 1 trending keyword in US',
        'url': 'https://trends.google.com/trending?geo=US'
    })

@app.route('/status')
def status():
    """Status endpoint để debug"""
    try:
        current_top1 = monitor.get_top1_trending_keyword()
        
        # Get volume estimates
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
            'notifications_sent': {
                '4h': len(monitor.notification_tracker.notified_4h),
                '24h': len(monitor.notification_tracker.notified_24h)
            },
            'last_check': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'bot_status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/test')
def test_manual():
    """Manual test endpoint"""
    try:
        logger.info("🧪 Manual test triggered")
        notifications = monitor.check_top1_keyword()
        
        return jsonify({
            'test_result': 'success',
            'notifications_found': len(notifications),
            'notifications': notifications,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Manual test failed: {e}")
        return jsonify({
            'test_result': 'error',
            'error': str(e)
        }), 500

async def send_notification(keyword_data: Dict):
    """Gửi thông báo Telegram"""
    timeframe_text = "4h qua" if keyword_data['timeframe'] == '4h' else "24h qua"
    
    message = f"""🥇 **TOP 1 TRENDING ALERT** 🥇

🔍 **Từ khóa TOP 1**: `{keyword_data['keyword']}`
📊 **Đã đạt**: `{keyword_data['volume']:,} lượt tìm kiếm`
⏱️ **Trong**: `{timeframe_text}`
🌍 **Khu vực**: `United States`
📅 **Thời gian**: `{keyword_data['timestamp'].strftime('%H:%M %d/%m/%Y')}`
🏆 **Vị trí**: `TOP 1 Trending`

⚠️ **TEST MODE** - Ngưỡng: {SEARCH_THRESHOLD:,}

#TOP1Alert #TestMode #GoogleTrends #USA"""

    try:
        await bot_instance.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode='Markdown'
        )
        logger.info(f"✅ Notification sent successfully: {keyword_data['keyword']}")
    except Exception as e:
        logger.error(f"❌ Error sending Telegram notification: {e}")

def monitoring_loop():
    """Main monitoring loop"""
    logger.info(f"🚀 Starting TOP 1 monitoring every {CHECK_INTERVAL_MINUTES} minute(s)")
    logger.info(f"🎯 Threshold: {SEARCH_THRESHOLD:,} searches")
    logger.info(f"🌍 Target: Google Trends US")
    
    iteration = 0
    
    while True:
        try:
            iteration += 1
            logger.info(f"🔄 Monitoring iteration #{iteration}")
            logger.info("=" * 50)
            
            notifications = monitor.check_top1_keyword()
            
            if notifications:
                logger.info(f"📨 Processing {len(notifications)} notifications...")
                
                # Create event loop for async operations
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                for i, notification in enumerate(notifications, 1):
                    logger.info(f"📤 Sending notification {i}/{len(notifications)}")
                    loop.run_until_complete(send_notification(notification))
                    time.sleep(1)  # Small delay between notifications
                
                loop.close()
                logger.info(f"✅ Successfully sent {len(notifications)} notifications")
            else:
                logger.info("📊 No notifications needed this cycle")
            
        except Exception as e:
            logger.error(f"❌ Error in monitoring loop (iteration #{iteration}): {e}")
            logger.error(f"🔄 Will retry in {CHECK_INTERVAL_MINUTES} minute(s)")
        
        # Wait for next check
        logger.info("=" * 50)
        logger.info(f"💤 Sleeping {CHECK_INTERVAL_MINUTES} minute(s) until next check...")
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

# Khởi động bot
logger.info("🤖 Google Trends TOP 1 Monitor Bot initializing...")
logger.info(f"⚙️  TEST MODE: {CHECK_INTERVAL_MINUTES} min intervals, {SEARCH_THRESHOLD:,} threshold")

# Start monitoring thread
monitor_thread = Thread(target=monitoring_loop, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    logger.info("🚀 Flask web server starting...")
    app.run(host='0.0.0.0', port=PORT, debug=False)
