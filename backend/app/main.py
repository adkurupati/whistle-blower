from fastapi import FastAPI
from sqlalchemy import text

from app.db import engine

app = FastAPI(title="WhistleBlower API")


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        return {"status": "degraded", "database": "unreachable", "error": str(exc)}
    return {"status": "ok", "database": db_status}
