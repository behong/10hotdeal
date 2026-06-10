from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from contextlib import asynccontextmanager
import psycopg2
import psycopg2.extras
import psycopg2.pool
import os
from dotenv import load_dotenv

load_dotenv()

# ── Rate Limiter 설정 ──
limiter = Limiter(key_func=get_remote_address)

# ── 캐시 초기화 ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    FastAPICache.init(InMemoryBackend())
    # DB 연결 풀 초기화 (최소 1개, 최대 5개)
    global db_pool
    db_pool = psycopg2.pool.SimpleConnectionPool(
        1, 5,
        os.getenv("DATABASE_URL")
    )
    yield
    # 앱 종료 시 풀 닫기
    db_pool.closeall()

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS 설정 (도메인 제한) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # "https://your-custom-domain.com",     # ✅ 커스텀 도메인 있으면 추가
        "https://hot.hongzi.us",          # ✅ 실제 도메인
        "http://localhost:5500",              # 로컬 테스트용
        "http://127.0.0.1:5500",             # 로컬 테스트용
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── DB 연결 풀 (전역) ──
db_pool = None

def get_conn():
    return db_pool.getconn()

def release_conn(conn):
    db_pool.putconn(conn)

# ── 이미지 URL 정리 ──
def clean_image_url(url):
    if not url or str(url).strip().lower() in ('none', 'null', ''):
        return None
    return url

# ── 이미지 정리 공통 함수 ──
def fill_images(rows):
    result = []
    for row in rows:
        deal = dict(row)
        deal["image_url"] = (
            clean_image_url(deal.get("image_url"))
            or clean_image_url(deal.get("image_source"))
        )
        result.append(deal)
    return result

# ── Health Check (DB 쿼리 포함 → Neon 슬립 방지) ──
@app.get("/health")
@app.head("/health")
def health():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")  # Neon DB 슬립 방지
        release_conn(conn)
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "ok", "db": "error"}

# ── 카테고리(seller_type) 목록 ──
@app.get("/api/categories")
@limiter.limit("30/minute")
@cache(expire=600)  # 10분 캐싱
async def get_categories(request: Request):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT seller_type, COUNT(*) as cnt
            FROM hot_deals
            WHERE seller_type IS NOT NULL
              AND seller_type != ''
              AND seller_type NOT ILIKE '%coupang%'
              AND seller_type NOT ILIKE '%쿠팡%'
            GROUP BY seller_type
            ORDER BY cnt DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        release_conn(conn)
        return [r["seller_type"] for r in rows if r["seller_type"]]
    except Exception as e:
        if 'conn' in locals(): release_conn(conn)
        raise HTTPException(status_code=500, detail="카테고리 로드 실패")

# ── 딜 목록 (페이지네이션 + 다중 seller 필터) ──
@app.get("/api/deals")
@limiter.limit("30/minute")
async def get_deals(request: Request, page: int = 1, limit: int = 40, sellers: str = ''):
    offset = (page - 1) * limit
    # 쉼표로 구분된 seller 목록 파싱
    seller_list = [s.strip() for s in sellers.split(',') if s.strip()] if sellers else []
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 전체 개수
        if seller_list:
            placeholders = ','.join(['%s'] * len(seller_list))
            cur.execute(
                f"SELECT COUNT(*) as count FROM hot_deals WHERE seller_type IN ({placeholders})",
                seller_list
            )
        else:
            cur.execute("SELECT COUNT(*) as count FROM hot_deals")
        total = cur.fetchone()["count"]

        # 페이지 데이터
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
            placeholders = ','.join(['%s'] * len(seller_list))
            cur.execute(
                select_sql + f"WHERE seller_type IN ({placeholders}) ORDER BY last_seen_at DESC LIMIT %s OFFSET %s",
                seller_list + [limit, offset]
            )
        else:
            cur.execute(
                select_sql + "ORDER BY last_seen_at DESC LIMIT %s OFFSET %s",
                (limit, offset)
            )
        rows = cur.fetchall()
        release_conn(conn)

        return {
            "items": fill_images(rows),
            "total": int(total),
            "page": page,
            "has_more": (offset + limit) < int(total)
        }
    except Exception as e:
        if 'conn' in locals(): release_conn(conn)
        raise HTTPException(status_code=500, detail="데이터를 불러올 수 없어요")

# ── 티커용 인기 딜 ──
@app.get("/api/ticker")
@limiter.limit("30/minute")
@cache(expire=300)  # 5분 캐싱
async def get_ticker(request: Request):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT title, product_name, price_text
            FROM hot_deals
            ORDER BY recommendation_count DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        release_conn(conn)
        return list(rows)
    except Exception as e:
        if 'conn' in locals(): release_conn(conn)
        raise HTTPException(status_code=500, detail="티커 데이터를 불러올 수 없어요")
    
    # ── 검색 ──
@app.get("/api/search")
@limiter.limit("30/minute")
async def search_deals(request: Request, q: str = ""):
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="검색어를 2자 이상 입력해주세요")
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
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
        """, (f"%{q}%", f"%{q}%"))
        rows = cur.fetchall()
        release_conn(conn)

        return fill_images(rows)
    except Exception as e:
        if 'conn' in locals(): release_conn(conn)
        raise HTTPException(status_code=500, detail="검색 중 오류가 발생했어요")