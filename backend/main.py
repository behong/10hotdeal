from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

@app.get("/api/deals")
def get_deals():
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
        ORDER BY last_seen_at DESC
        LIMIT 50
    """)
    rows = cur.fetchall()
    conn.close()
    return list(rows)

@app.get("/health")
def health():
    return {"status": "ok"}