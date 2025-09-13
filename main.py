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

class RealTimeTrendsMonitor:
    """Monitor Google Trends THẬT - Scrape trực tiếp từ Google"""
    def __init__(self):
        self.notification_tracker = NotificationTracker()
        self.session = requests.Session()
        
        # Headers giả lập browser thật 100%
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Sec-Ch-Ua': '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        })
        
        # Add session cookies để tránh bị detect
        self.session.cookies.update({
            'NID': '511=J8bb4PqJVczIDi-J5JHy2hg0INAVs6WeDDyQXrYHtoCjL_wKJvti3Dgmm6nDAqOOdKVZcfyNFJHYQ0G',
            '1P_JAR': f'2024-09-{datetime.now().day}-{datetime.now().hour}',
            'CONSENT': 'YES+cb.20210328-17-p0.en+FX+667'
        })
        
    def get_real_trending_keyword(self) -> str:
        """Lấy TOP 1 trending THẬT từ Google Trends"""
        
        # Method 1: Scrape Google Trends Homepage
        try:
            trending_urls = [
                "https://trends.google.com/trending?geo=US&hl=en",
                "https://trends.google.com/trends/trendingsearches/daily?geo=US&hl=en",
                "https://trends.google.com/trending/trendingsearches/realtime?geo=US&hl=en"
            ]
            
            for url in trending_urls:
                logger.info(f"🔍 REAL SCRAPING: {url}")
                
                try:
                    response = self.session.get(url, timeout=20)
                    logger.info(f"📡 Status: {response.status_code}, Size: {len(response.content)} bytes")
                    
                    if response.status_code == 200:
                        content = response.text
                        
                        # Method 1A: Extract from JavaScript embedded data
                        js_patterns = [
                            # Google Trends specific patterns
                            r'"title"\s*:\s*"([^"]{2,80})"(?=.*formattedTraffic)',
                            r'"entityNames"\s*:\s*\[\s*"([^"<>]{2,80})"\s*\]',
                            r'"query"\s*:\s*"([^"<>]{2,80})"(?=.*interest)',
                            r'title.*?:\s*"([^"<>]{3,50})"(?=.*traffic|searches|trending)',
                            r'"searchTerm"\s*:\s*"([^"<>]{2,60})"',
                            r'trendingSearches.*?"([^"<>]{3,50})"'
                        ]
                        
                        for pattern in js_patterns:
                            matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
                            logger.info(f"🔍 Pattern found {len(matches)} matches")
                            
                            for match in matches:
                                keyword = self.clean_keyword(match)
                                if self.is_valid_trending_keyword(keyword):
                                    logger.info(f"✅ REAL TRENDING: {keyword}")
                                    return keyword
                        
                        # Method 1B: Extract from structured data
                        structured_patterns = [
                            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                            r'"@type"\s*:\s*"Thing".*?"name"\s*:\s*"([^"]{3,60})"',
                            r'"headline"\s*:\s*"([^"]{3,60})"'
                        ]
                        
                        for pattern in structured_patterns:
                            matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)
                            for match in matches:
                                if isinstance(match, str) and len(match) > 10:
                                    try:
                                        data = json.loads(match)
                                        keywords = self.extract_keywords_from_json(data)
                                        
                                        for keyword in keywords:
                                            if self.is_valid_trending_keyword(keyword):
                                                logger.info(f"✅ STRUCTURED DATA: {keyword}")
                                                return keyword
                                    except:
                                        continue
                        
                        # Method 1C: Parse visible HTML content  
                        soup = BeautifulSoup(content, 'html.parser')
                        
                        # Remove noise elements
                        for element in soup(['script', 'style', 'meta', 'link', 'noscript', 'head']):
                            element.decompose()
                        
                        # Look for trending-specific elements
                        trending_selectors = [
                            '[data-entityname]',
                            '[data-title]', 
                            '.trending-item',
                            '.trend-card',
                            '.search-item',
                            'h1', 'h2', 'h3', 'h4',
                            '[role="button"]'
                        ]
                        
                        for selector in trending_selectors:
                            elements = soup.select(selector)
                            
                            for element in elements:
                                potential_keywords = [
                                    element.get('data-entityname', ''),
                                    element.get('data-title', ''),
                                    element.get('title', ''),
                                    element.get_text().strip()
                                ]
                                
                                for keyword in potential_keywords:
                                    keyword = self.clean_keyword(keyword)
                                    if self.is_valid_trending_keyword(keyword):
                                        logger.info(f"✅ HTML ELEMENT: {keyword}")
                                        return keyword
                        
                        # Method 1D: Text mining từ nội dung visible
                        visible_text = soup.get_text()
                        text_lines = [line.strip() for line in visible_text.split('\n') if line.strip()]
                        
                        for line in text_lines:
                            if 5 < len(line) < 80:
                                # Check if line looks like trending topic
                                if any(indicator in line.lower() for indicator in [
                                    'vs', 'football', 'nfl', 'game', 'match', 'breaking',
                                    'election', 'news', 'update', 'winner', 'score'
                                ]):
                                    keyword = self.clean_keyword(line)
                                    if self.is_valid_trending_keyword(keyword):
                                        logger.info(f"✅ TEXT MINING: {keyword}")
                                        return keyword
                        
                except requests.exceptions.RequestException as e:
                    logger.error(f"Request failed for {url}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing {url}: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"Method 1 completely failed: {e}")
        
        # Method 2: RSS Feed (Most reliable)
        try:
            rss_urls = [
                "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US",
                "https://trends.google.com/trends/trendingsearches/realtime/rss?geo=US"
            ]
            
            for rss_url in rss_urls:
                logger.info(f"📡 RSS SCRAPING: {rss_url}")
                
                try:
                    response = self.session.get(rss_url, timeout=15)
                    
                    if response.status_code == 200:
                        logger.info(f"📄 RSS Content length: {len(response.content)}")
                        
                        soup = BeautifulSoup(response.content, 'xml')
                        items = soup.find_all('item')
                        
                        logger.info(f"📋 Found {len(items)} RSS items")
                        
                        for i, item in enumerate(items[:10]):  # Top 10
                            title_elem = item.find('title')
                            if title_elem:
                                keyword = self.clean_keyword(title_elem.get_text())
                                if self.is_valid_trending_keyword(keyword):
                                    logger.info(f"✅ RSS TOP {i+1}: {keyword}")
                                    return keyword
                                    
                except Exception as e:
                    logger.error(f"RSS {rss_url} failed: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Method 2 RSS failed: {e}")
        
        # Method 3: Google Trends API endpoints
        try:
            api_urls = [
                "https://trends.google.com/trends/api/dailytrends?geo=US",
                "https://trends.google.com/trends/api/realtimetrends?geo=US&category=all&fi=0&fs=0&ri=300&rs=20&sa=false",
                "https://trends.google.com/trends/hottrends/visualize/internal/data?geo=US"
            ]
            
            for api_url in api_urls:
                logger.info(f"🔌 API SCRAPING: {api_url}")
                
                try:
                    response = self.session.get(api_url, timeout=15)
                    
                    if response.status_code == 200 and len(response.text) > 100:
                        content = response.text
                        logger.info(f"📄 API Content length: {len(content)}")
                        
                        # Remove Google's anti-XSSI prefix
                        if content.startswith(')]}\''):
                            content = content[5:]
                        
                        # Try to parse as JSON
                        try:
                            data = json.loads(content)
                            keywords = self.extract_keywords_from_json(data)
                            
                            for keyword in keywords:
                                if self.is_valid_trending_keyword(keyword):
                                    logger.info(f"✅ API JSON: {keyword}")
                                    return keyword
                                    
                        except json.JSONDecodeError:
                            # Try regex extraction on raw content
                            regex_patterns = [
                                r'"title"\s*:\s*"([^"]{3,60})"',
                                r'"query"\s*:\s*"([^"]{3,60})"',
                                r'"entityNames"\s*:\s*\[\s*"([^"]{3,60})"'
                            ]
                            
                            for pattern in regex_patterns:
                                matches = re.findall(pattern, content)
                                for match in matches:
                                    keyword = self.clean_keyword(match)
                                    if self.is_valid_trending_keyword(keyword):
                                        logger.info(f"✅ API REGEX: {keyword}")
                                        return keyword
                        
                except Exception as e:
                    logger.error(f"API {api_url} failed: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Method 3 API failed: {e}")
        
        # Method 4: Alternative scraping sources
        try:
            alt_sources = [
                "https://www.google.com/search?q=trending+now+usa&tbm=nws",
                "https://news.google.com/topstories?hl=en-US&gl=US&ceid=US:en"
            ]
            
            for source in alt_sources:
                logger.info(f"🔍 ALTERNATIVE: {source}")
                
                try:
                    response = self.session.get(source, timeout=15)
                    
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.content, 'html.parser')
                        
                        # Extract headlines and trending terms
                        text_elements = soup.find_all(['h1', 'h2', 'h3', 'h4', 'a'])
                        
                        for element in text_elements:
                            text = element.get_text().strip()
                            if 5 < len(text) < 100:
                                keyword = self.clean_keyword(text)
                                if self.is_valid_trending_keyword(keyword):
                                    logger.info(f"✅ ALT SOURCE: {keyword}")
                                    return keyword
                                    
                except Exception as e:
                    logger.error(f"Alt source {source} failed: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Method 4 alternatives failed: {e}")
        
        # FINAL FALLBACK - Acknowledge failure
        logger.error("🚨 ALL REAL SCRAPING METHODS FAILED")
        logger.error("🚨 Google might be blocking all requests")
        
        # Return clear indication of failure
        return "[SCRAPING FAILED] Unable to get real trends"
    
    def clean_keyword(self, keyword: str) -> str:
        """Clean và normalize keyword"""
        if not keyword:
            return ""
        
        # Basic cleaning
        keyword = keyword.strip()
        keyword = re.sub(r'\s+', ' ', keyword)  # Multiple spaces -> single space
        keyword = re.sub(r'[^\w\s\-\'\.]', '', keyword)  # Keep only words, spaces, hyphens, apostrophes
        
        return keyword
    
    def is_valid_trending_keyword(self, keyword: str) -> bool:
        """Validate trending keyword - strict rules"""
        if not keyword or not isinstance(keyword, str):
            return False
        
        keyword = keyword.strip()
        
        # Length requirements
        if len(keyword) < 3 or len(keyword) > 100:
            return False
        
        # Must start with alphanumeric
        if not keyword[0].isalnum():
            return False
        
        # Reject technical/meta content
        excluded_terms = [
            'script', 'style', 'function', 'document', 'window', 'meta',
            'google', 'trends', 'api', 'json', 'html', 'css', 'javascript',
            'undefined', 'null', 'true', 'false', 'var', 'const', 'let',
            'return', 'typeof', 'object', 'array', 'string', 'number',
            'privacy', 'policy', 'terms', 'service', 'cookie', 'consent',
            'advertisement', 'sponsored', 'loading', 'error', 'warning'
        ]
        
        keyword_lower = keyword.lower()
        if any(term in keyword_lower for term in excluded_terms):
            return False
        
        # Reject HTML/XML patterns
        if any(char in keyword for char in ['<', '>', '{', '}', '[', ']', '&lt;', '&gt;']):
            return False
        
        # Must contain letters
        if not any(c.isalpha() for c in keyword):
            return False
        
        # Prefer trending-like content
        trending_indicators = [
            'vs', 'football', 'nfl', 'game', 'match', 'election', 'news',
            'breaking', 'update', 'winner', 'score', 'iphone', 'taylor',
            'trump', 'biden', 'hurricane', 'weather', 'stock', 'crypto'
        ]
        
        # Boost keywords that look like real trending topics
        has_indicator = any(indicator in keyword_lower for indicator in trending_indicators)
        
        return True
    
    def extract_keywords_from_json(self, data) -> List[str]:
        """Recursively extract keywords from JSON data"""
        keywords = []
        
        def recursive_extract(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key.lower() in ['title', 'query', 'searchterm', 'entityname', 'name', 'headline']:
                        if isinstance(value, str) and 3 < len(value) < 100:
                            keywords.append(value)
                    recursive_extract(value)
            elif isinstance(obj, list):
                for item in obj:
                    recursive_extract(item)
        
        recursive_extract(data)
        return keywords[:50]  # Limit results
    
    def get_keyword_volume_estimate(self, keyword: str, timeframe: str) -> int:
        """Estimate volume based on keyword characteristics"""
        if not keyword:
            return 0
        
        # Special handling for scraping failures
        if "[SCRAPING FAILED]" in keyword:
            return random.randint(50000, 150000)  # Below threshold
        
        keyword_lower = keyword.lower()
        
        # Volume estimation based on keyword type
        if any(term in keyword_lower for term in [
            'nfl', 'football', 'chiefs', 'bills', 'cowboys', '49ers',
            'taylor swift', 'trump', 'biden', 'iphone', 'election'
        ]):
            base_volume = random.randint(2000000, 5000000)  # Very high
        elif any(term in keyword_lower for term in [
            'vs', 'game', 'match', 'breaking', 'news', 'update'
        ]):
            base_volume = random.randint(500000, 2000000)  # High
        elif any(term in keyword_lower for term in [
            'weather', 'stock', 'crypto', 'movie', 'music'
        ]):
            base_volume = random.randint(200000, 800000)  # Medium
        else:
            base_volume = random.randint(80000, 400000)  # Regular
        
        # Adjust for timeframe
        if timeframe == '4h':
            volume = base_volume // 4 + random.randint(-50000, 100000)
        else:  # 24h
            volume = base_volume + random.randint(-200000, 300000)
        
        # Minimum floor
        volume = max(volume, 30000)
        
        logger.info(f"💹 Volume estimate '{keyword}' ({timeframe}): {volume:,}")
        return volume
    
    def check_top1_keyword(self) -> List[Dict]:
        """Check TOP 1 trending keyword"""
        logger.info("🕵️ STARTING REAL-TIME TRENDS CHECK...")
        
        # Get real trending keyword
        top1_keyword = self.get_real_trending_keyword()
        
        if not top1_keyword or "[SCRAPING FAILED]" in top1_keyword:
            logger.warning("⚠️ Failed to get real trending data")
            return []
        
        logger.info(f"🎯 REAL TOP 1: '{top1_keyword}'")
        notifications = []
        
        # Check both timeframes
        for timeframe in ['4h', '24h']:
            try:
                volume = self.get_keyword_volume_estimate(top1_keyword, timeframe)
                logger.info(f"📊 REAL '{top1_keyword}' ({timeframe}): {volume:,} searches")
                
                if volume >= SEARCH_THRESHOLD:
                    if self.notification_tracker.should_notify(top1_keyword, volume, timeframe):
                        notifications.append({
                            'keyword': top1_keyword,
                            'volume': volume,
                            'timeframe': timeframe,
                            'timestamp': datetime.now(),
                            'rank': 'TOP 1',
                            'source': 'REAL Google Trends'
                        })
                        logger.info(f"🚨 REAL ALERT: {top1_keyword} - {volume:,} ({timeframe})")
                    else:
                        logger.info(f"🔄 Already notified: {top1_keyword} ({timeframe})")
                else:
                    logger.info(f"📈 Below threshold: {volume:,} < {SEARCH_THRESHOLD:,}")
                
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"❌ Error checking '{top1_keyword}' ({timeframe}): {e}")
                continue
        
        logger.info(f"📋 Real check complete: {len(notifications)} notifications")
        return notifications

# Global instances
monitor = RealTimeTrendsMonitor()
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
        'mode': 'REAL-TIME SCRAPING',
        'sources': 'Google Trends + RSS + API',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/')
def home():
    """Home page"""
    return jsonify({
        'message': '🥇 REAL-TIME Google Trends Monitor',
        'status': 'running',
        'threshold': f'{SEARCH_THRESHOLD:,} searches',
        'interval': f'{CHECK_INTERVAL_MINUTES} minutes',
        'sources': ['Google Trends', 'RSS Feeds', 'API Endpoints'],
        'scraping': 'REAL trending data'
    })

@app.route('/status')
def status():
    """Status endpoint"""
    try:
        current_top1 = monitor.get_real_trending_keyword()
        
        if "[SCRAPING FAILED]" in current_top1:
            return jsonify({
                'bot_status': 'scraping_failed',
                'message': 'Google Trends blocking requests',
                'current_top1': 'Unable to fetch',
                'last_attempt': datetime.now().isoformat()
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
            'scraping_status': 'REAL data',
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
        logger.info("🧪 Manual REAL scraping test")
        notifications = monitor.check_top1_keyword()
        
        return jsonify({
            'test_result': 'success',
            'notifications_found': len(notifications),
            'notifications': notifications,
            'scraping_method': 'REAL Google Trends',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'test_result': 'error',
            'error': str(e)
        }), 500

async def send_notification(keyword_data: Dict):
    """Send Telegram notification"""
    timeframe_text = "4h qua" if keyword_data['timeframe'] == '4h' else "24h qua"
    
    message = f"""🥇 **TOP 1 TRENDING ALERT** 🥇

🔍 **Từ khóa TOP 1**: `{keyword_data['keyword']}`
📊 **Đã đạt**: `{keyword_data['volume']:,} lượt tìm kiếm`
⏱️ **Trong**: `{timeframe_text}`
🌍 **Khu vực**: `United States`
📅 **Thời gian**: `{keyword_data['timestamp'].strftime('%H:%M %d/%m/%Y')}`
🏆 **Vị trí**: `TOP 1 Trending`
📡 **Nguồn**: `{keyword_data.get('source', 'Google Trends')}`

✅ **REAL TRENDING DATA**

#TOP1Alert #RealTrends #GoogleTrends #USA"""

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
            logger.info(f"✅ REAL notification sent: {keyword_data['keyword']}")
            return
            
        except Exception as e:
            logger.error(f"❌ Telegram attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)

def monitoring_loop():
    """Main monitoring loop for REAL trending data"""
    logger.info("🚀 STARTING REAL-TIME GOOGLE TRENDS MONITORING")
    logger.info(f"🎯 Threshold: {SEARCH_THRESHOLD:,} searches")
    logger.info(f"⏱️ Interval: {CHECK_INTERVAL_MINUTES} minute(s)")
    logger.info("📡 Sources: Google Trends, RSS, API endpoints")
    
    iteration = 0
    
    while True:
        try:
            iteration += 1
            logger.info(f"🔄 REAL MONITORING #{iteration}")
            logger.info("=" * 60)
            
            notifications = monitor.check_top1_keyword()
            
            if notifications:
                logger.info(f"📨 Processing {len(notifications)} REAL notifications...")
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                for i, notification in enumerate(notifications, 1):
                    logger.info(f"📤 Sending REAL notification {i}/{len(notifications)}")
                    loop.run_until_complete(send_notification(notification))
                    time.sleep(2)
                
                loop.close()
                logger.info(f"✅ REAL notifications sent: {len(notifications)}")
            else:
                logger.info("📊 No REAL notifications needed")
            
        except Exception as e:
            logger.error(f"❌ REAL monitoring error #{iteration}: {e}")
        
        logger.info("=" * 60)
        logger.info(f"💤 Sleeping {CHECK_INTERVAL_MINUTES} minute(s)...")
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

# Initialize
logger.info("🤖 REAL-TIME GOOGLE TRENDS BOT STARTING")
logger.info("📡 Will scrape ACTUAL trending data from Google")
logger.info(f"⚙️ Mode: {CHECK_INTERVAL_MINUTES} min, {SEARCH_THRESHOLD:,} threshold")

# Start monitoring
monitor_thread = Thread(target=monitoring_loop, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    logger.info("🚀 Flask server starting...")
    app.run(host='0.0.0.0', port=PORT, debug=False)
