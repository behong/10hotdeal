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
import os
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Rate Limiter 설정 ──
limiter = Limiter(key_func=get_remote_address)

# ── 캐시 초기화 ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    FastAPICache.init(InMemoryBackend())
    yield

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

# ── DB 연결 ──
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

# ── 네이버 쇼핑 이미지 검색 ──
def get_naver_image(query: str) -> str:
    try:
        res = requests.get(
            "https://openapi.naver.com/v1/search/shop.json",
            headers={
                "X-Naver-Client-Id": os.getenv("NAVER_CLIENT_ID"),
                "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET"),
            },
            params={"query": query, "display": 1, "sort": "sim"},
            timeout=3
        )
        items = res.json().get("items", [])
        return items[0]["image"] if items else None
    except Exception:
        return None

# ── Health Check ──
@app.get("/health")
@app.head("/health")
def health():
    return {"status": "ok"}

# ── 딜 목록 (페이지네이션) ──
@app.get("/api/deals")
@limiter.limit("30/minute")
async def get_deals(request: Request, page: int = 1, limit: int = 20):
    offset = (page - 1) * limit
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 전체 개수
        cur.execute("SELECT COUNT(*) as count FROM hot_deals")
        total = cur.fetchone()["count"]

        # 페이지 데이터
        cur.execute("""
            SELECT
                title, product_name, price_text,
                image_url, image_source,
                seller_type, seller_url,
                affiliate_url, source_url, source,
                recommendation_count, comment_count,
                last_seen_at
            FROM hot_deals
            ORDER BY last_seen_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        rows = cur.fetchall()
        conn.close()

        # 이미지 없는 항목 네이버로 보완
        result = []
        for row in rows:
            deal = dict(row)
            if not deal.get("image_url") and not deal.get("image_source"):
                query = deal.get("product_name") or deal.get("title") or ""
                if query:
                    deal["image_url"] = get_naver_image(query)
            result.append(deal)

        return {
            "items": result,
            "total": int(total),
            "page": page,
            "has_more": (offset + limit) < int(total)
        }
    except Exception as e:
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
        conn.close()
        return list(rows)
    except Exception as e:
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
        conn.close()

        # 이미지 없는 항목 네이버로 보완
        result = []
        for row in rows:
            deal = dict(row)
            if not deal.get("image_url") and not deal.get("image_source"):
                query = deal.get("product_name") or deal.get("title") or ""
                if query:
                    deal["image_url"] = get_naver_image(query)
            result.append(deal)

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="검색 중 오류가 발생했어요")