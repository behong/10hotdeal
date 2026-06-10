from contextlib import asynccontextmanager
from pathlib import Path
import os

import psycopg2
import psycopg2.extras
import psycopg2.pool
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = Path("C:/Users/Administrator/code/핫딜웹-260521")
FRONTEND_DIR = SOURCE_DIR / "frontend"

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
FRONTEND_DIR = Path(os.getenv("HOTDEAL_FRONTEND_DIR", ROOT_DIR / "frontend"))

load_dotenv(APP_DIR / ".env", override=False)
load_dotenv(ROOT_DIR / ".env", override=False)

limiter = Limiter(key_func=get_remote_address)
db_pool: psycopg2.pool.SimpleConnectionPool | None = None


def database_url() -> str:
    return os.getenv("DATABASE_URL") or os.getenv("LOCAL_DATABASE_URL", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    FastAPICache.init(InMemoryBackend())
    global db_pool
    db_url = database_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL or LOCAL_DATABASE_URL is required")
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, db_url)
    yield
    if db_pool:
        db_pool.closeall()


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hot.hongzi.us",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.mount("/icons", StaticFiles(directory=FRONTEND_DIR / "icons"), name="icons")


def get_conn():
    if db_pool is None:
        raise RuntimeError("database pool is not initialized")
    return db_pool.getconn()


def release_conn(conn) -> None:
    if db_pool is not None:
        db_pool.putconn(conn)


def clean_image_url(url):
    if not url or str(url).strip().lower() in ("none", "null", ""):
        return None
    return url


def fill_images(rows):
    result = []
    for row in rows:
        deal = dict(row)
        deal["image_url"] = clean_image_url(deal.get("image_url")) or clean_image_url(deal.get("image_source"))
        result.append(deal)
    return result


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("const API_URL = 'https://one0hotdeal.onrender.com';", "const API_URL = '';")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    path = FRONTEND_DIR / "robots.txt"
    return path.read_text(encoding="utf-8") if path.exists() else "User-agent: *\nAllow: /\n"


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(FRONTEND_DIR / "icons" / "favicon-32x32.png", media_type="image/png")


@app.get("/health")
@app.head("/health")
def health():
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        return {"status": "ok", "db": "ok"}
    except Exception:
        return {"status": "ok", "db": "error"}
    finally:
        if conn is not None:
            release_conn(conn)


@app.get("/api/categories")
@limiter.limit("30/minute")
@cache(expire=600)
async def get_categories(request: Request):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT seller_type, COUNT(*) as cnt
            FROM hot_deals
            WHERE seller_type IS NOT NULL
              AND seller_type != ''
            GROUP BY seller_type
            ORDER BY
              CASE
                WHEN seller_type ILIKE '%쿠팡%' OR seller_type ILIKE '%coupang%' THEN 1
                WHEN seller_type ILIKE '%11번가%' OR seller_type ILIKE '%11st%' THEN 2
                WHEN seller_type ILIKE '%지마켓%' OR seller_type ILIKE '%gmarket%' THEN 3
                WHEN seller_type ILIKE '%옥션%' OR seller_type ILIKE '%auction%' THEN 4
                WHEN seller_type ILIKE '%네이버%' OR seller_type ILIKE '%naver%' THEN 5
                WHEN seller_type ILIKE '%알리%' OR seller_type ILIKE '%ali%' THEN 6
                ELSE 99
              END,
              cnt DESC,
              seller_type ASC
            LIMIT 12
            """
        )
        rows = cur.fetchall()
        return [r["seller_type"] for r in rows if r["seller_type"]]
    except Exception:
        raise HTTPException(status_code=500, detail="쇼핑몰 필터 로드 실패")
    finally:
        if conn is not None:
            release_conn(conn)


@app.get("/api/deals")
@limiter.limit("30/minute")
async def get_deals(request: Request, page: int = 1, limit: int = 40, sellers: str = ""):
    offset = (page - 1) * limit
    seller_list = [s.strip() for s in sellers.split(",") if s.strip()] if sellers else []
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if seller_list:
            placeholders = ",".join(["%s"] * len(seller_list))
            cur.execute(f"SELECT COUNT(*) as count FROM hot_deals WHERE seller_type IN ({placeholders})", seller_list)
        else:
            cur.execute("SELECT COUNT(*) as count FROM hot_deals")
        total = cur.fetchone()["count"]

        select_sql = """
            SELECT
                title, product_name, price_text,
                image_url, image_source,
                seller_type, seller_url,
                affiliate_url, source_url, source,
                recommendation_count, comment_count,
                last_seen_at
            FROM hot_deals
        """
        if seller_list:
            placeholders = ",".join(["%s"] * len(seller_list))
            cur.execute(
                select_sql + f"WHERE seller_type IN ({placeholders}) ORDER BY last_seen_at DESC LIMIT %s OFFSET %s",
                seller_list + [limit, offset],
            )
        else:
            cur.execute(select_sql + "ORDER BY last_seen_at DESC LIMIT %s OFFSET %s", (limit, offset))
        rows = cur.fetchall()

        return {
            "items": fill_images(rows),
            "total": int(total),
            "page": page,
            "has_more": (offset + limit) < int(total),
        }
    except Exception:
        raise HTTPException(status_code=500, detail="데이터를 불러올 수 없어요")
    finally:
        if conn is not None:
            release_conn(conn)


@app.get("/api/ticker")
@limiter.limit("30/minute")
@cache(expire=300)
async def get_ticker(request: Request):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT title, product_name, price_text
            FROM hot_deals
            ORDER BY recommendation_count DESC
            LIMIT 10
            """
        )
        return list(cur.fetchall())
    except Exception:
        raise HTTPException(status_code=500, detail="티커 데이터를 불러올 수 없어요")
    finally:
        if conn is not None:
            release_conn(conn)


@app.get("/api/search")
@limiter.limit("30/minute")
async def search_deals(request: Request, q: str = ""):
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="검색어를 2자 이상 입력해주세요")
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                title,
                product_name,
                price_text,
                image_url,
                image_source,
                seller_type,
                seller_url,
                affiliate_url,
                source_url,
                source,
                recommendation_count,
                comment_count,
                last_seen_at
            FROM hot_deals
            WHERE title ILIKE %s
               OR product_name ILIKE %s
            ORDER BY last_seen_at DESC
            LIMIT 50
            """,
            (f"%{q}%", f"%{q}%"),
        )
        return fill_images(cur.fetchall())
    except Exception:
        raise HTTPException(status_code=500, detail="검색 중 오류가 발생했어요")
    finally:
        if conn is not None:
            release_conn(conn)
