import os
import glob
import gzip
import json
import time
import shutil
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta, date, datetime, timezone

import boto3
import duckdb
import requests
import pytz
import holidays
from botocore.client import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ENDPOINT = 'https://files.massive.com'
BUCKET = 'flatfiles'
BASE_REMOTE_PATH = "us_stocks_sip/minute_aggs_v1/"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
MINUTE_DATA_DIR = os.path.join(DATA_DIR, "minute_data")
NEWS_DIR = os.path.join(DATA_DIR, "news")

START_DATE = date(2025, 1, 1)
END_DATE = date.today()
DATA_YEARS = range(START_DATE.year, END_DATE.year + 1)
MAX_RETRIES = 3
DOWNLOAD_WORKERS = int(os.getenv("DOWNLOAD_WORKERS", "8"))
NEWS_WORKERS = int(os.getenv("NEWS_WORKERS", "6"))
                                                                                
                                                                                
                                                 
LOAD_BATCH_SIZE = int(os.getenv("LOAD_BATCH_SIZE", "5"))
                                                                             
                                                                              
                                                                              
                                                                               
FRESH_LOAD_BATCH_SIZE = int(os.getenv("FRESH_LOAD_BATCH_SIZE", "10"))
                                                                             
                                                                            
                                                                             
DUCKDB_MEMORY_LIMIT = os.getenv("DUCKDB_MEMORY_LIMIT", "24GB")
DUCKDB_TEMP_DIR = os.path.join(DATA_DIR, "duckdb_tmp")

NY_TZ = pytz.timezone('America/New_York')
us_holidays = holidays.US(years=DATA_YEARS)

                                                                             
                                                                               
                                                                                
                                                                   
DB_PATH = os.path.abspath(os.path.join(BASE_DIR, os.getenv("DB_PATH", "market_data.duckdb")))

NEWS_CONFIG = {
    "DB_PATH": DB_PATH,
    "MASSIVE_API_KEY": os.getenv("MASSIVE_API_KEY"),
    "MASSIVE_API_URL": os.getenv("MASSIVE_API_URL", "https://api.massive.com/v2/reference/news"),
    "MASSIVE_API_SECRET": os.getenv("MASSIVE_API_SECRET"),
}

DUCKDB_CONFIG = {
    "DB_PATH": DB_PATH,
    "CSV_SOURCES": [
        os.path.join(MINUTE_DATA_DIR, f"minute_{year}", "**", "*.csv")
        for year in DATA_YEARS
    ],
    "TABLE_NAME": "minute_bars",
    "THREADS": 8,
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


class StockDataDownloader:

    def __init__(self, s3_client):
        self.s3 = s3_client
        self.stats = {"downloaded": 0, "skipped": 0, "failed": 0, "holiday": 0, "not_available": 0}
        self._stats_lock = threading.Lock()

    def _incr(self, key):
        with self._stats_lock:
            self.stats[key] += 1

    def is_trading_day(self, check_date):
        if check_date.weekday() >= 5:
            return False
        if check_date in us_holidays:
            return False
        return True

    def is_data_available(self, current_date):
        yr = current_date.strftime("%Y")
        mo = current_date.strftime("%m")
        dt_str = current_date.strftime("%Y-%m-%d")
        remote_key = f"{BASE_REMOTE_PATH}{yr}/{mo}/{dt_str}.csv.gz"
        try:
            self.s3.head_object(Bucket=BUCKET, Key=remote_key)
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "?")
            logger.warning(f"head_object {remote_key} -> ClientError {code}: {e}")
            return False
        except Exception as e:
            logger.warning(f"head_object {remote_key} -> {type(e).__name__}: {e}")
            return False

    def existing_db_days(self):
        """Set of trading days already present in minute_bars (so we skip them)."""
        try:
            conn = duckdb.connect(database=NEWS_CONFIG["DB_PATH"], read_only=True)
            days = {
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT ts_ny::DATE FROM {DUCKDB_CONFIG['TABLE_NAME']}"
                ).fetchall()
            }
            conn.close()
            return days
        except Exception as e:
            logger.warning(f"Could not read existing days from DB: {e}")
            return set()

    def download_and_convert(self):
                                                                                   
                                                                                  
                                                                               
        existing = self.existing_db_days()
        logger.info(f"Scanning {START_DATE} → {END_DATE}; {len(existing)} day(s) already in DB")

        trading_days = []
        current_date = START_DATE
        while current_date <= END_DATE:
            if not self.is_trading_day(current_date):
                if current_date in us_holidays:
                    self._incr("holiday")
            elif current_date in existing:
                self._incr("skipped")                                            
            else:
                trading_days.append(current_date)
            current_date += timedelta(days=1)

        logger.info(f"{len(trading_days)} missing trading day(s) to download")

        total = len(trading_days)
        logger.info(f"Downloading {total} trading day(s) with {DOWNLOAD_WORKERS} workers...")
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
            futures = {executor.submit(self._handle_date, d): d for d in trading_days}
            for done, future in enumerate(as_completed(futures), start=1):
                d = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Unhandled error for {d.strftime('%Y-%m-%d')}: {e}")
                    self._incr("failed")
                if done % 25 == 0 or done == total:
                    logger.info(f"  Progress: {done}/{total} days processed")

        logger.info(f"\n{'='*60}")
        logger.info("Stock data download complete!")
        logger.info(f"Downloaded: {self.stats['downloaded']}, Skipped: {self.stats['skipped']}, Failed: {self.stats['failed']}, Holidays: {self.stats['holiday']}, Not Available: {self.stats['not_available']}")
        logger.info(f"{'='*60}\n")

    @staticmethod
    def _local_path(current_date):
        yr = current_date.strftime("%Y")
        mo = current_date.strftime("%m")
        dt_str = current_date.strftime("%Y-%m-%d")
        local_dir = os.path.join(MINUTE_DATA_DIR, f"minute_{yr}", mo)
        return os.path.join(local_dir, f"{dt_str}.csv")

    def _handle_date(self, current_date):
                                                                               
                                                                           
                                                                              
                      
        if os.path.exists(self._local_path(current_date)):
            logger.info(f"Skipping: {current_date.strftime('%Y-%m-%d')}.csv (Already exists)")
            self._incr("skipped")
            return
        if not self.is_data_available(current_date):
            logger.info(f"Data not available: {current_date.strftime('%Y-%m-%d')} (Will try later)")
            self._incr("not_available")
            return
        self._process_date(current_date)

    def _process_date(self, current_date):
        yr = current_date.strftime("%Y")
        mo = current_date.strftime("%m")
        dt_str = current_date.strftime("%Y-%m-%d")

        remote_filename = f"{dt_str}.csv.gz"
        local_filename = f"{dt_str}.csv"
        remote_key = f"{BASE_REMOTE_PATH}{yr}/{mo}/{remote_filename}"
        local_dir = os.path.join(MINUTE_DATA_DIR, f"minute_{yr}", mo)
        local_path = os.path.join(local_dir, local_filename)
        temp_gz_path = local_path + ".gz"

        try:
            os.makedirs(local_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create directory {local_dir}: {e}")
            self._incr("failed")
            return

        if os.path.exists(local_path):
            logger.info(f"Skipping: {local_filename} (Already exists)")
            self._incr("skipped")
            return

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"Downloading: {remote_filename} (Attempt {attempt}/{MAX_RETRIES})...")
                self.s3.download_file(BUCKET, remote_key, temp_gz_path)

                with gzip.open(temp_gz_path, 'rb') as f_in:
                    with open(local_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)

                os.remove(temp_gz_path)
                logger.info(f"✅ Converted and saved: {local_path}")
                self._incr("downloaded")
                break
            except ClientError as e:
                logger.warning(f"S3 error for {dt_str} (attempt {attempt}): {e}")
                if attempt == MAX_RETRIES:
                    logger.error(f"Failed to download {remote_filename} after {MAX_RETRIES} attempts")
                    if os.path.exists(temp_gz_path):
                        os.remove(temp_gz_path)
                    self._incr("failed")
            except Exception as e:
                logger.error(f"Error processing {dt_str}: {e}")
                if os.path.exists(temp_gz_path):
                    os.remove(temp_gz_path)
                self._incr("failed")
                break


class HistoricalNewsDownloader:

    @staticmethod
    def initialize_news_schema():
        try:
            conn = duckdb.connect(database=NEWS_CONFIG["DB_PATH"], read_only=False)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_news (
                    news_id VARCHAR,
                    ticker VARCHAR,
                    news_date DATE,
                    published_utc TIMESTAMP,
                    title VARCHAR,
                    sentiment VARCHAR,
                    source VARCHAR,
                    PRIMARY KEY(news_id, ticker)
                )
            """)
            conn.close()
            logger.info("✅ News table schema verified/created")
            return True
        except Exception as e:
            logger.error(f"Failed to verify news table schema: {e}")
            return False

    @staticmethod
    def _get_last_news_db_datetime():
        try:
            conn = duckdb.connect(database=NEWS_CONFIG["DB_PATH"], read_only=True)
            row = conn.execute("SELECT MAX(published_utc) FROM market_news").fetchone()
            conn.close()
            if row and row[0] is not None:
                return row[0] if isinstance(row[0], datetime) else None
        except Exception as e:
            logger.warning(f"Could not query market_news for last datetime: {e}")
        return None

    @staticmethod
    def _get_last_news_file_date():
        try:
            news_files = glob.glob(os.path.join(NEWS_DIR, "*", "*", "*.json"), recursive=True)
            if not news_files:
                return None
            latest = None
            for fp in news_files:
                try:
                    parts = os.path.basename(fp).replace('.json', '').split('-')
                    if len(parts) == 3:
                        d, m, y = parts
                        fdate = date(int(y), int(m), int(d))
                        if latest is None or fdate > latest:
                            latest = fdate
                except Exception:
                    continue
            return latest
        except Exception as e:
            logger.warning(f"Error reading news files: {e}")
            return None

    @staticmethod
    def _get_news_fetch_end_date():
        csv_dates = _csv_dates_on_disk()
        if not csv_dates:
            logger.warning("  No local minute CSV files found; skipping API news fetch")
            return None

        fetch_end = min(max(csv_dates), END_DATE)
        logger.info(f"  Last available minute CSV date: {fetch_end}")
        return fetch_end

    @staticmethod
    def get_last_news_file_date(fetch_end_date):
        db_dt = HistoricalNewsDownloader._get_last_news_db_datetime()
        fs_date = HistoricalNewsDownloader._get_last_news_file_date()

        logger.info(f"  Last news datetime in DB    : {db_dt}")
        logger.info(f"  Last news date on filesystem: {fs_date}")

        candidates = []
        if db_dt is not None:
            candidates.append(db_dt.date())
        if fs_date is not None:
            candidates.append(fs_date)

        if not candidates:
            logger.info(f"  No prior news found — starting from {START_DATE}")
            return START_DATE

        resume_date = min(max(candidates), fetch_end_date)
        logger.info(f"  Resuming news fetch from {resume_date} to {fetch_end_date} (re-checking last day for updates)")
        return resume_date

    @staticmethod
    def _parse_articles(articles):
        day_news = []
        parsed_rows = []
        for article in articles:
            news_id = article.get("id")
            title = article.get("title")
            published_str = article.get("published_utc")
            if not news_id or not title or not published_str:
                continue

            try:
                published_clean = published_str.replace('Z', '').split('.')[0]
                published_utc = datetime.strptime(published_clean, "%Y-%m-%dT%H:%M:%S")
                published_ny = published_utc.replace(tzinfo=timezone.utc).astimezone(NY_TZ).replace(tzinfo=None)
                news_date = published_ny.date()
            except Exception:
                continue

            publisher_info = article.get("publisher", {})
            source = publisher_info.get("name", "Unknown") if isinstance(publisher_info, dict) else str(publisher_info)

            insights = article.get("insights", [])
            sentiment = insights[0].get("sentiment", "neutral") if insights else "neutral"

            tickers = article.get("tickers", []) or ["GLOBAL"]

            day_news.append(article)
            for ticker in tickers:
                ticker_str = ticker if isinstance(ticker, str) else ticker.get("symbol", "GLOBAL")
                parsed_rows.append((
                    news_id,
                    ticker_str.strip().upper(),
                    news_date,
                    published_ny,
                    title,
                    sentiment,
                    source,
                ))
        return day_news, parsed_rows

    @staticmethod
    def _write_day_json(current_date, day_news):
        year = current_date.strftime("%Y")
        month = current_date.strftime("%m")
        day = current_date.strftime("%d")

        news_dir = os.path.join(NEWS_DIR, year, month)
        try:
            os.makedirs(news_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create news directory {news_dir}: {e}")
            return

        json_filename = f"{day}-{month}-{year}.json"
        json_file = os.path.join(news_dir, json_filename)

        existing_news = []
        if os.path.exists(json_file):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    existing_news = loaded
            except Exception as e:
                logger.warning(f"Could not read existing {json_filename} ({e}); it will be rewritten")

        existing_ids = {a.get("id") for a in existing_news if isinstance(a, dict)}
        new_articles = [a for a in day_news if a.get("id") not in existing_ids]

        if not os.path.exists(json_file) or new_articles:
            merged_news = existing_news + new_articles
            try:
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(merged_news, f, indent=2, ensure_ascii=False)
                if new_articles:
                    logger.info(f"✅ Added {len(new_articles)} new news records to {json_file} (total {len(merged_news)})")
                elif merged_news:
                    logger.info(f"✅ Saved {len(merged_news)} news records to {json_file}")
                else:
                    logger.info(f"✅ Saved empty news file {json_file} (no news for this day)")
            except Exception as e:
                logger.error(f"Failed to write JSON file {json_file}: {e}")
        else:
            logger.info(f"Skipping: {json_filename} (Already up to date, no new articles)")

    @staticmethod
    def _fetch_one_day(current_date, api_key):
        date_str = current_date.strftime("%Y-%m-%d")
        logger.info(f"Fetching news for {date_str}...")

        url = "https://api.massive.com/v2/reference/news"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        params = {
            "published_utc.gte": f"{date_str}T00:00:00Z",
            "published_utc.lte": f"{date_str}T23:59:59Z",
            "limit": 1000,
            "sort": "published_utc",
            "order": "ascending",
        }

        day_news = []
        parsed_rows = []
        next_url = url
        while next_url:
            try:
                if next_url == url:
                    response = requests.get(url, headers=headers, params=params, timeout=20)
                else:
                    response = requests.get(next_url, headers=headers, timeout=20)

                if response.status_code != 200:
                    logger.warning(f"API returned HTTP status {response.status_code} for {date_str}")
                    break

                payload = response.json()
                articles = payload.get("results", [])
                logger.info(f"Found {len(articles)} articles for {date_str}")

                page_news, page_rows = HistoricalNewsDownloader._parse_articles(articles)
                day_news.extend(page_news)
                parsed_rows.extend(page_rows)

                next_url = payload.get("next_url")
            except Exception as e:
                logger.warning(f"Error fetching news for {date_str}: {e}")
                break

        HistoricalNewsDownloader._write_day_json(current_date, day_news)
        return parsed_rows

    @staticmethod
    def _market_news_is_empty(conn):
        """True if market_news has no rows (or doesn't exist) -> fast fresh load."""
        try:
            return conn.execute("SELECT 1 FROM market_news LIMIT 1").fetchone() is None
        except Exception:
            return True

    @staticmethod
    def _news_file_date(fp):
        """Date encoded in a news JSON filename (DD-MM-YYYY.json), or None."""
        try:
            d, m, y = os.path.basename(fp).replace('.json', '').split('-')
            return date(int(y), int(m), int(d))
        except Exception:
            return None

    @staticmethod
    def _finalize_market_news_pk(conn):
        """De-dup (news_id, ticker) then (re)build the PRIMARY KEY once.

        Used by the fast path, which loads with no constraint. A given article
        lands in exactly one day's JSON, so duplicates are rare (same ticker
        listed twice on one article), but we de-dup defensively so ADD PRIMARY
        KEY can't fail.
        """
        conn.execute("""
            CREATE OR REPLACE TABLE market_news AS
            SELECT news_id, ticker, news_date, published_utc, title, sentiment, source
            FROM market_news
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY news_id, ticker ORDER BY published_utc DESC NULLS LAST
            ) = 1
        """)
        try:
            conn.execute("ALTER TABLE market_news ADD PRIMARY KEY (news_id, ticker);")
        except Exception as e:
            logger.warning(f"ADD PRIMARY KEY on market_news failed ({e}); using UNIQUE INDEX")
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS market_news_uq "
                    "ON market_news(news_id, ticker);"
                )
            except Exception as e2:
                logger.error(f"Could not enforce uniqueness on market_news ({e2})")

    @staticmethod
    def import_local_news_json():
        """Back-fill market_news from every JSON file already on disk.

        The API resume logic (get_last_news_file_date) starts from the last file
        date, so it will NOT re-insert historical JSON into the DB. After a fresh
        DB this would leave thousands of already-downloaded articles unimported.
        This loads them straight from disk (no API calls), deduped by PRIMARY KEY.

        Fast path: when market_news is empty (e.g. a from-scratch build with no
        prior API fetch), we recreate the table WITHOUT the PRIMARY KEY, append
        every row with no per-row ON CONFLICT/index probe, and build the key once
        at the end -- the same trick the minute_bars loader uses. When the table
        already has rows (e.g. the API fetch ran first) we keep the safe
        ON CONFLICT merge so we never clobber existing data.
        """
        news_files = sorted(glob.glob(os.path.join(NEWS_DIR, "*", "*", "*.json"), recursive=True))
        if not news_files:
            logger.info("No local news JSON files to import")
            return True

        try:
            conn = duckdb.connect(database=NEWS_CONFIG["DB_PATH"], read_only=False)
        except Exception as e:
            logger.error(f"Could not open DB for news import: {e}")
            return False

                                                                              
                                                                              
                                                                                 
                                                                              
                                                                           
                                                                               
                                                                            
                                                                          
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_import_log (
                file_path VARCHAR PRIMARY KEY,
                file_mtime DOUBLE,
                file_date DATE,
                imported_at TIMESTAMP
            )
        """)

        def _mtime(fp):
            try:
                return os.path.getmtime(fp)
            except OSError:
                return None

        fresh = HistoricalNewsDownloader._market_news_is_empty(conn)

                                                                                
                                                         
        if not fresh:
            try:
                logged = {
                    r[0]: r[1] for r in conn.execute(
                        "SELECT file_path, file_mtime FROM news_import_log"
                    ).fetchall()
                }
            except Exception as e:
                logger.warning(f"Could not read news_import_log ({e}); importing all files")
                logged = {}

            if logged:
                kept = [
                    fp for fp in news_files
                    if fp not in logged or _mtime(fp) != logged[fp]
                ]
                logger.info(
                    f"{len(news_files) - len(kept)} file(s) already imported (unchanged); "
                    f"importing {len(kept)} new/changed file(s)"
                )
                news_files = kept
                if not news_files:
                    logger.info("✅ market_news already up to date with local JSON")
                    conn.close()
                    return True

        logger.info(f"Importing {len(news_files)} local news JSON file(s) into market_news...")
        if fresh:
            logger.info("market_news empty -> fast bulk-load (no ON CONFLICT; PRIMARY KEY built at the end)")
            conn.execute("DROP TABLE IF EXISTS market_news;")
            conn.execute("""
                CREATE TABLE market_news (
                    news_id VARCHAR,
                    ticker VARCHAR,
                    news_date DATE,
                    published_utc TIMESTAMP,
                    title VARCHAR,
                    sentiment VARCHAR,
                    source VARCHAR
                )
            """)

                                                                                 
                                                                                 
                                                                               
                                                                             
        def _read_one(fp):
            mtime = _mtime(fp)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"Could not read {fp}: {e}")
                return None                                  
            rows = []
            if isinstance(data, list):
                _, rows = HistoricalNewsDownloader._parse_articles(data)
            return fp, mtime, HistoricalNewsDownloader._news_file_date(fp), rows

                                                                                
                                                                          
        processed = []
        all_rows = []
        with ThreadPoolExecutor(max_workers=NEWS_WORKERS) as executor:
            for done, res in enumerate(executor.map(_read_one, news_files), start=1):
                if res is not None:
                    fp, mtime, fdate, rows = res
                    processed.append((fp, mtime, fdate))
                    all_rows.extend(rows)
                if done % 100 == 0 or done == len(news_files):
                    logger.info(f"  Parsed {done}/{len(news_files)} file(s) ({len(all_rows):,} rows)")
        total = len(all_rows)

        try:
            conn.execute("BEGIN TRANSACTION;")
            if all_rows:
                if fresh:
                                                                                  
                                                                             
                    logger.info(f"  Loading {total:,} row(s) into market_news...")
                    conn.executemany(
                        "INSERT INTO market_news "
                        "(news_id, ticker, news_date, published_utc, title, sentiment, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        all_rows,
                    )
                else:
                                                                               
                                                                                   
                                                                                 
                                                                    
                    logger.info(f"  Staging {total:,} row(s)...")
                    conn.execute("""
                        CREATE TEMP TABLE _news_stage (
                            news_id VARCHAR, ticker VARCHAR, news_date DATE,
                            published_utc TIMESTAMP, title VARCHAR,
                            sentiment VARCHAR, source VARCHAR
                        )
                    """)
                    conn.executemany(
                        "INSERT INTO _news_stage VALUES (?, ?, ?, ?, ?, ?, ?)",
                        all_rows,
                    )
                    logger.info("  Merging staged rows into market_news (anti-join)...")
                    conn.execute("""
                        INSERT INTO market_news
                        SELECT s.news_id, s.ticker, s.news_date, s.published_utc,
                               s.title, s.sentiment, s.source
                        FROM (
                            SELECT news_id, ticker, news_date, published_utc,
                                   title, sentiment, source
                            FROM _news_stage
                            QUALIFY ROW_NUMBER() OVER (
                                PARTITION BY news_id, ticker
                                ORDER BY published_utc DESC NULLS LAST
                            ) = 1
                        ) s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM market_news m
                            WHERE m.news_id = s.news_id AND m.ticker = s.ticker
                        )
                    """)
                    conn.execute("DROP TABLE _news_stage;")
            if processed:
                conn.executemany(
                    """
                    INSERT INTO news_import_log (file_path, file_mtime, file_date, imported_at)
                    VALUES (?, ?, ?, now()::TIMESTAMP)
                    ON CONFLICT (file_path) DO UPDATE SET
                        file_mtime = excluded.file_mtime,
                        file_date = excluded.file_date,
                        imported_at = excluded.imported_at
                    """,
                    processed,
                )
            conn.execute("COMMIT;")
            if fresh:
                HistoricalNewsDownloader._finalize_market_news_pk(conn)
            logger.info("  Checkpointing...")
            conn.execute("CHECKPOINT;")
            logger.info(f"✅ Local news import done ({total:,} rows prepared, deduped on insert)")
            return True
        except Exception as e:
            logger.error(f"Error importing local news JSON: {e}")
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            return False
        finally:
            conn.close()

    @staticmethod
    def fetch_and_save_news_by_ticker():
        api_key = os.getenv("MASSIVE_API_KEY") or os.getenv("API_KEY") or os.getenv("NEWSAPI_KEY")
        if not api_key:
            logger.error("MASSIVE_API_KEY not found in environment variables")
            logger.error("Expected: MASSIVE_API_KEY, API_KEY, or NEWSAPI_KEY")
            return False

        NEWS_CONFIG["MASSIVE_API_KEY"] = api_key
        logger.info(f"Using API key: {api_key[:10]}...")

        try:
            fetch_end_date = HistoricalNewsDownloader._get_news_fetch_end_date()
            if fetch_end_date is None:
                return True

            start = HistoricalNewsDownloader.get_last_news_file_date(fetch_end_date)
            days = []
            d = start
            while d <= fetch_end_date:
                days.append(d)
                d += timedelta(days=1)

            all_parsed_rows = []
            logger.info(f"Fetching {len(days)} news day(s) with {NEWS_WORKERS} workers...")
            with ThreadPoolExecutor(max_workers=NEWS_WORKERS) as executor:
                futures = {executor.submit(HistoricalNewsDownloader._fetch_one_day, day, api_key): day for day in days}
                for future in as_completed(futures):
                    day = futures[future]
                    try:
                        all_parsed_rows.extend(future.result())
                    except Exception as e:
                        logger.error(f"Unhandled error fetching {day.strftime('%Y-%m-%d')}: {e}")

            if all_parsed_rows:
                logger.info(f"Syncing {len(all_parsed_rows)} total news records to database...")
                try:
                    conn = duckdb.connect(database=NEWS_CONFIG["DB_PATH"], read_only=False)
                    conn.executemany("""
                        INSERT INTO market_news (news_id, ticker, news_date, published_utc, title, sentiment, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (news_id, ticker) DO NOTHING
                    """, all_parsed_rows)
                    conn.close()
                    logger.info(f"✅ Successfully synced {len(all_parsed_rows)} news records to database")
                except Exception as e:
                    logger.error(f"Error syncing to database: {e}")
                    return False
            else:
                logger.info("No news records to sync to database")

            logger.info(f"Total news records processed: {len(all_parsed_rows)}")
            return True
        except Exception as e:
            logger.error(f"Error during news fetch: {e}")
            return False


class DuckDBDataLoader:

    @staticmethod
    def find_csv_files():
        csv_files = []
        base_paths = [os.path.join(MINUTE_DATA_DIR, f"minute_{year}") for year in DATA_YEARS]
        for base_path in base_paths:
            if os.path.exists(base_path):
                pattern = os.path.join(base_path, "**", "*.csv")
                csv_files.extend(glob.glob(pattern, recursive=True))

        if csv_files:
            logger.info(f"Found {len(csv_files)} CSV files to process")
            return csv_files
        logger.warning(f"No CSV files found in {', '.join(base_paths)} directories")
        return []

    @staticmethod
    def initialize_minute_bars_schema():
        try:
            conn = duckdb.connect(database=DUCKDB_CONFIG["DB_PATH"], read_only=False)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS minute_bars (
                    ticker VARCHAR,
                    volume INTEGER,
                    open DOUBLE,
                    close DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    window_start VARCHAR,
                    transactions INTEGER,
                    ts_utc TIMESTAMP,
                    ts_ny TIMESTAMP,
                    PRIMARY KEY(ticker, window_start)
                )
            """)
            conn.close()
            logger.info("✅ Minute bars table schema verified/created")
            return True
        except Exception as e:
            logger.error(f"Failed to verify minute_bars table schema: {e}")
            return False

    @staticmethod
    def _file_date(path):
        try:
            return datetime.strptime(os.path.basename(path).replace('.csv', ''), "%Y-%m-%d").date()
        except Exception:
            return None

    @staticmethod
    def _table_is_empty(con, table):
        """True if `table` has no rows (or doesn't exist) -> treat as a fresh load."""
        try:
            return con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None
        except Exception:
            return True

    @staticmethod
    def _build_insert_query(file_list):
        """INSERT for the incremental path.

        De-dups within the scanned files (see _dedup_select), then skips any row
        whose (ticker, window_start) is already in the table via a NOT EXISTS
        anti-join. This is used instead of ON CONFLICT DO NOTHING because the
        table may have NO unique/primary-key constraint or index (the fresh
        load's ADD PRIMARY KEY / unique-index step can fail on some DuckDB
        builds), and ON CONFLICT requires such a target. The anti-join needs
        none, so the load always works.
        """
        table = DUCKDB_CONFIG['TABLE_NAME']
        source = f"read_csv_auto({DuckDBDataLoader._file_list_sql(file_list)}, union_by_name=true)"
        return f"""
        INSERT INTO {table}
        SELECT src.* FROM (
            {DuckDBDataLoader._dedup_select(source)}
        ) AS src
        WHERE NOT EXISTS (
            SELECT 1 FROM {table} t
            WHERE t.ticker = src.ticker AND t.window_start = src.window_start
        )
        ORDER BY src.ts_ny, src.ticker
        """

    @staticmethod
    def _report_after_load(con, added_count, failed_files, start_time):
        if failed_files:
            logger.warning(
                f"{len(failed_files)} file(s) could not be loaded and were skipped:"
            )
            for f in failed_files:
                logger.warning(f"    - {f}")
            logger.warning("Re-run to retry them (load is gap-aware).")

        elapsed = time.time() - start_time
        if added_count and added_count > 0:
            rate = added_count / elapsed if elapsed > 0 else 0
            logger.info(f"Success! Added {added_count:,} new rows to minute_bars")
            logger.info(f"Time elapsed: {elapsed:.2f}s ({rate:,.0f} rows/s)")
            logger.info("Recent Samples (NY Time)")
            samples = con.execute(
                f"SELECT ticker, ts_ny, close FROM {DUCKDB_CONFIG['TABLE_NAME']} ORDER BY ts_ny DESC LIMIT 5"
            ).fetchall()
            for ticker, ts_ny, close in samples:
                logger.info(f"  {ticker:<8} {ts_ny}  {close}")
        else:
            logger.info("No new data found - database is up to date")

    @staticmethod
    def _dedup_select(source_sql):
        """The canonical SELECT that reads/casts CSV columns and de-dups
        (ticker, window_start). `source_sql` is whatever produces the raw rows
        (a read_csv_auto(...) call). Columns are cast to the canonical
        minute_bars types so a CTAS-created table matches the declared schema.
        """
        return f"""
        SELECT
            ticker, volume, open, close, high, low, window_start, transactions,
            ts_utc,
            (ts_utc AT TIME ZONE 'America/New_York') AS ts_ny
        FROM (
            SELECT
                CAST(ticker AS VARCHAR)        AS ticker,
                CAST(volume AS INTEGER)        AS volume,
                CAST(open AS DOUBLE)           AS open,
                CAST(close AS DOUBLE)          AS close,
                CAST(high AS DOUBLE)           AS high,
                CAST(low AS DOUBLE)            AS low,
                CAST(window_start AS VARCHAR)  AS window_start,
                CAST(transactions AS INTEGER)  AS transactions,
                TRY_CAST(window_start AS BIGINT) AS ws_num,
                to_timestamp(TRY_CAST(window_start AS BIGINT) / 1e9) AS ts_utc
            FROM {source_sql}
        ) AS raw_data
        WHERE ws_num IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY ticker, window_start
            ORDER BY transactions DESC NULLS LAST
        ) = 1
        ORDER BY ts_ny, ticker
        """

    @staticmethod
    def _file_list_sql(file_list):
        return "[" + ", ".join(
            "'" + f.replace("'", "''") + "'" for f in file_list
        ) + "]"

    @staticmethod
    def _fresh_batched_load(con, csv_files, start_time):
        """Fallback for the fresh path: append files in batches (no PK / no
        ON CONFLICT), retrying file-by-file so one bad CSV can't drop a batch.
        Returns (added_count, failed_files). Assumes the table does NOT exist
        yet (the single-pass CTAS is tried first)."""
        table = DUCKDB_CONFIG['TABLE_NAME']
        con.execute(f"DROP TABLE IF EXISTS {table};")
        con.execute(f"""
            CREATE TABLE {table} (
                ticker VARCHAR, volume INTEGER, open DOUBLE, close DOUBLE,
                high DOUBLE, low DOUBLE, window_start VARCHAR, transactions INTEGER,
                ts_utc TIMESTAMP, ts_ny TIMESTAMP
            );
        """)
        batches = [
            csv_files[i:i + FRESH_LOAD_BATCH_SIZE]
            for i in range(0, len(csv_files), FRESH_LOAD_BATCH_SIZE)
        ]
        logger.info(
            f"Batched fallback: {len(csv_files)} file(s) in {len(batches)} "
            f"batch(es) of up to {FRESH_LOAD_BATCH_SIZE}"
        )
        added_count = 0
        failed_files = []

        def _insert(file_list):
            res = con.execute(
                f"INSERT INTO {table} "
                + DuckDBDataLoader._dedup_select(
                    f"read_csv_auto({DuckDBDataLoader._file_list_sql(file_list)}, union_by_name=true)"
                )
            ).fetchone()
            return res[0] if res else 0

        for bi, batch in enumerate(batches, start=1):
            try:
                added_count += _insert(batch)
                logger.info(
                    f"  Batch {bi}/{len(batches)}: total {added_count:,} rows "
                    f"({time.time() - start_time:.1f}s)"
                )
            except Exception as e:
                logger.warning(f"  Batch {bi}/{len(batches)} failed ({e}); retrying file-by-file...")
                try:
                    con.execute("ROLLBACK;")
                except Exception:
                    pass
                for f in batch:
                    try:
                        added_count += _insert([f])
                        logger.info(f"    ✅ {os.path.basename(f)}: ok")
                    except Exception as e2:
                        failed_files.append(f)
                        logger.error(f"    ❌ {os.path.basename(f)} FAILED: {e2}")
                        try:
                            con.execute("ROLLBACK;")
                        except Exception:
                            pass
        return added_count, failed_files

    @staticmethod
    def _fresh_load(con, csv_files):
        """Fast path for a fresh / empty minute_bars table.

        Each daily CSV is self-contained (one trading day), so duplicate
        (ticker, window_start) rows only occur WITHIN a file -- never across
        files -- and on a fresh DB there are no already-committed rows to
        collide with. So we skip BOTH the PRIMARY KEY index and ON CONFLICT
        during the load and build the index ONCE at the very end.

        FASTEST: do the whole thing as a single CREATE TABLE AS SELECT over a
        glob of every file. DuckDB reads all CSVs in parallel across threads,
        de-dups in one pass, and writes the table once -- no Python loop, no
        per-batch query planning. If that fails (e.g. memory_limit too low for
        the one-shot window de-dup, or a malformed CSV), we fall back to the
        resilient batched loader. Bump DUCKDB_MEMORY_LIMIT to keep the single
        pass winning on large loads.
        """
        table = DUCKDB_CONFIG['TABLE_NAME']
        start_time = time.time()
        logger.info(
            f"{table} is empty -> fast bulk-load path "
            f"(single-pass CTAS; PRIMARY KEY built at the end)"
        )

        added_count = 0
        failed_files = []
        try:
            logger.info(f"Single-pass load of all {len(csv_files)} file(s) in one query...")
            con.execute(f"DROP TABLE IF EXISTS {table};")
            con.execute(
                f"CREATE TABLE {table} AS "
                + DuckDBDataLoader._dedup_select(
                    f"read_csv_auto({DuckDBDataLoader._file_list_sql(csv_files)}, union_by_name=true)"
                )
            )
            added_count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            logger.info(f"Single-pass load done ({time.time() - start_time:.1f}s)")
        except Exception as e:
            logger.warning(f"Single-pass load failed ({e}); using batched fallback...")
            try:
                con.execute("ROLLBACK;")
            except Exception:
                pass
            added_count, failed_files = DuckDBDataLoader._fresh_batched_load(
                con, csv_files, start_time
            )

        con.execute("CHECKPOINT;")

                                                                             
                                                                                 
                                                     
        logger.info("Building PRIMARY KEY(ticker, window_start)...")
        try:
            con.execute(f"ALTER TABLE {table} ADD PRIMARY KEY (ticker, window_start);")
        except Exception as e:
            logger.warning(f"ADD PRIMARY KEY failed ({e}); falling back to UNIQUE INDEX")
            try:
                con.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_uq "
                    f"ON {table}(ticker, window_start);"
                )
            except Exception as e2:
                logger.error(
                    f"Could not create a uniqueness constraint ({e2}); incremental "
                    f"runs may fail on ON CONFLICT. Data was loaded; inspect for dupes."
                )
        con.execute("CHECKPOINT;")

        DuckDBDataLoader._report_after_load(con, added_count, failed_files, start_time)
        return True

    @staticmethod
    def load_csv_data():
        db_path = DUCKDB_CONFIG["DB_PATH"]
        if not os.path.exists(db_path):
            logger.error(f"Database file '{db_path}' not found")
            return False

        csv_files = DuckDBDataLoader.find_csv_files()
        if not csv_files:
            logger.warning("No CSV files found to load")
            return False

        con = duckdb.connect(db_path)
        try:
            os.makedirs(DUCKDB_TEMP_DIR, exist_ok=True)
            con.execute("SET TimeZone='UTC';")
            con.execute(f"PRAGMA threads={DUCKDB_CONFIG['THREADS']};")
            con.execute("PRAGMA preserve_insertion_order=false;")
            con.execute(f"PRAGMA memory_limit='{DUCKDB_MEMORY_LIMIT}';")
            con.execute(f"PRAGMA temp_directory='{DUCKDB_TEMP_DIR}';")
            con.execute("SET enable_progress_bar=true;")
            con.execute("SET enable_progress_bar_print=true;")
            con.execute("SET progress_bar_time=500;")

                                                                                   
                                                                                   
            if DuckDBDataLoader._table_is_empty(con, DUCKDB_CONFIG['TABLE_NAME']):
                ok = DuckDBDataLoader._fresh_load(con, csv_files)
                con.close()
                return ok

                                                                                 
                                                                              
                                                                                
                                                                           
                                                                            
            try:
                existing_days = {
                    r[0] for r in con.execute(
                        f"SELECT DISTINCT ts_ny::DATE FROM {DUCKDB_CONFIG['TABLE_NAME']}"
                    ).fetchall()
                }
            except Exception as e:
                logger.error(f"Error querying database: {e}")
                con.close()
                return False

            logger.info(f"{len(existing_days)} trading day(s) already in the DB")
            files_to_scan = [
                f for f in csv_files
                if (DuckDBDataLoader._file_date(f) is None
                    or DuckDBDataLoader._file_date(f) not in existing_days)
            ]

            if not files_to_scan:
                logger.info("No new CSV files to load - database is up to date")
                con.close()
                return True

            logger.info(f"Scanning {len(files_to_scan)} of {len(csv_files)} CSV file(s)")

            batches = [
                files_to_scan[i:i + LOAD_BATCH_SIZE]
                for i in range(0, len(files_to_scan), LOAD_BATCH_SIZE)
            ]
            logger.info(f"Loading in {len(batches)} batch(es) of up to {LOAD_BATCH_SIZE} file(s)")

            added_count = 0
            failed_files = []
            start_time = time.time()

            def _insert_files(file_list):
                """INSERT one or more CSV files; returns rows added. Raises on error.

                Dedup note: the CSV files themselves contain duplicate
                (ticker, window_start) rows. We de-duplicate in-query with QUALIFY
                ROW_NUMBER() (keeping the row with the most transactions), and a
                NOT EXISTS anti-join then skips rows already in the table -- see
                _build_insert_query for why we don't rely on ON CONFLICT here.
                """
                result = con.execute(
                    DuckDBDataLoader._build_insert_query(file_list)
                ).fetchone()
                return result[0] if result else 0

            for bi, batch in enumerate(batches, start=1):
                                                                                   
                                                                                      
                                                                                       
                try:
                    added = _insert_files(batch)
                    added_count += added
                    con.execute("CHECKPOINT;")
                    logger.info(
                        f"  Batch {bi}/{len(batches)}: +{added:,} rows "
                        f"(total {added_count:,}, {time.time() - start_time:.1f}s)"
                    )
                except Exception as e:
                    logger.warning(
                        f"  Batch {bi}/{len(batches)} failed ({e}); retrying file-by-file..."
                    )
                    try:
                        con.execute("ROLLBACK;")
                    except Exception:
                        pass
                    for f in batch:
                        try:
                            added = _insert_files([f])
                            added_count += added
                            con.execute("CHECKPOINT;")
                            logger.info(f"    ✅ {os.path.basename(f)}: +{added:,} rows")
                        except Exception as e2:
                            failed_files.append(f)
                            logger.error(f"    ❌ {os.path.basename(f)} FAILED: {e2}")
                            try:
                                con.execute("ROLLBACK;")
                            except Exception:
                                pass

            DuckDBDataLoader._report_after_load(con, added_count, failed_files, start_time)
            con.close()
            return True
        except Exception as e:
            logger.error(f"Error during data loading: {e}")
            con.close()
            return False


def _csv_dates_on_disk():
    """Set of trading-day dates derived from CSV filenames on disk."""
    dates = set()
    for year in DATA_YEARS:
        path = os.path.join(MINUTE_DATA_DIR, f"minute_{year}")
        for fp in glob.glob(os.path.join(path, "**", "*.csv"), recursive=True):
            name = os.path.basename(fp).replace(".csv", "")
            try:
                dates.add(datetime.strptime(name, "%Y-%m-%d").date())
            except ValueError:
                pass
    return dates


def print_last_timestamps():
    """Show the latest timestamp already stored in each table, so it's clear the
    download/load then APPENDS after these points (both phases are gap-aware)."""
    print("\n" + "=" * 70)
    print("LAST TIMESTAMP ALREADY IN DATABASE (new data is appended after these)")
    print("=" * 70)

    db_path = DUCKDB_CONFIG["DB_PATH"]
    if not os.path.exists(db_path):
        logger.info(f"DB file: {db_path} (does not exist yet)")
        logger.info("No database yet -> starting fresh from %s", START_DATE)
        return

    size_gb = os.path.getsize(db_path) / (1024 ** 3)
    logger.info(f"DB file: {db_path}  ({size_gb:.2f} GB)")

    try:
        con = duckdb.connect(db_path, read_only=True)
    except Exception as e:
        logger.warning(f"Could not open DB to read last timestamps: {e}")
        return
    try:
        for label, table, col in (
            ("minute_bars ", DUCKDB_CONFIG["TABLE_NAME"], "ts_ny"),
            ("market_news ", "market_news", "published_utc"),
        ):
            try:
                last, n = con.execute(
                    f"SELECT MAX({col}), COUNT(*) FROM {table}"
                ).fetchone()
                if last is None:
                    logger.info(f"  {label}: empty (no rows yet)")
                else:
                    logger.info(f"  {label}: last {col} = {last}  ({n:,} rows)")
            except Exception:
                logger.info(f"  {label}: table not present yet")
    finally:
        con.close()


def verify_completeness():
    """Confirm every CSV day and every news article on disk made it into the DB."""
    print("\n" + "=" * 70)
    print("PHASE 1.4: VERIFY COMPLETENESS")
    print("=" * 70)

    db_path = DUCKDB_CONFIG["DB_PATH"]
    if not os.path.exists(db_path):
        logger.error(f"Database not found: {db_path}")
        return False

    ok = True
    con = duckdb.connect(db_path, read_only=True)
    try:
                                                                
        disk_days = _csv_dates_on_disk()
        db_days = {
            r[0] for r in con.execute(
                f"SELECT DISTINCT ts_ny::DATE FROM {DUCKDB_CONFIG['TABLE_NAME']}"
            ).fetchall()
        }
        rows = con.execute(
            f"SELECT COUNT(*) FROM {DUCKDB_CONFIG['TABLE_NAME']}").fetchone()[0]
        missing_days = sorted(disk_days - db_days)

        logger.info(f"minute_bars: {rows:,} rows, {len(db_days)} day(s) in DB, "
                    f"{len(disk_days)} CSV file(s) on disk")
        if missing_days:
            ok = False
            logger.warning(f"❌ {len(missing_days)} CSV day(s) NOT in DB:")
            for d in missing_days:
                logger.warning(f"    - {d}")
        else:
            logger.info("✅ All CSV days are loaded into minute_bars")

                                                                          
        news_files = glob.glob(os.path.join(NEWS_DIR, "*", "*", "*.json"), recursive=True)
        disk_ids = set()
        for fp in news_files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for a in data:
                        if isinstance(a, dict) and a.get("id"):
                            disk_ids.add(a["id"])
            except Exception:
                pass
        db_ids = {r[0] for r in con.execute(
            "SELECT DISTINCT news_id FROM market_news").fetchall()}
        missing_news = disk_ids - db_ids

        logger.info(f"market_news: {len(db_ids):,} unique article(s) in DB, "
                    f"{len(disk_ids):,} on disk ({len(news_files)} file(s))")
        if missing_news:
            ok = False
            logger.warning(f"❌ {len(missing_news):,} article(s) on disk NOT in DB")
        else:
            logger.info("✅ All on-disk news articles are in market_news")

    finally:
        con.close()

    if ok:
        logger.info("✅ VERIFICATION PASSED — database matches disk")
    else:
        logger.warning("⚠️  VERIFICATION FAILED — re-run load_data.py to fill gaps")
    return ok


def main():
    load_dotenv()

    print("\n" + "=" * 70)
    print("🕌 MAMDOUH - PHASE 1: STOCK DATA COLLECTION & NEWS AGGREGATION")
    print("=" * 70)

    print_last_timestamps()

    print("\n" + "=" * 70)
    print("PHASE 1.1: STOCK MINUTE AGGREGATES")
    print("=" * 70)

    access_key = os.getenv("MASSIVE_ACCESS_KEY")
    secret_key = os.getenv("MASSIVE_SECRET_KEY")
    if not access_key or not secret_key:
        logger.error("Missing MASSIVE_ACCESS_KEY or MASSIVE_SECRET_KEY")
        return

    s3 = boto3.client(
        's3',
        endpoint_url=ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version='s3v4'),
    )

    StockDataDownloader(s3).download_and_convert()

    print("\n" + "=" * 70)
    print("PHASE 1.2: HISTORICAL MARKET NEWS")
    print("=" * 70)

    if not HistoricalNewsDownloader.initialize_news_schema():
        logger.warning("Skipping news due to schema initialization failure")
    else:
        api_key = os.getenv("MASSIVE_API_KEY")
        if api_key:
            logger.info("Fetching news for all tickers...")
            HistoricalNewsDownloader.fetch_and_save_news_by_ticker()
        else:
            logger.warning("News API credentials not configured, skipping API fetch")

                                                                            
                                                                           
                                                                           
        HistoricalNewsDownloader.import_local_news_json()
        print("\n" + "=" * 70)
        print("News step completed")
        print("=" * 70)

    print("\n" + "=" * 70)
    print("PHASE 1.3: LOAD CSV DATA INTO DUCKDB")
    print("=" * 70)

    if not DuckDBDataLoader.initialize_minute_bars_schema():
        logger.warning("Skipping CSV load due to schema initialization failure")
        return

    if not DuckDBDataLoader.load_csv_data():
        logger.warning("CSV data loading encountered errors")

    verify_completeness()

    print("\n" + "=" * 70)
    print("✅ MAMDOUH PHASE 1 COMPLETE")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
