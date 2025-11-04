#!/usr/bin/env python3
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    import orjson

    def _json_dumps_str(obj) -> str:
        
        return orjson.dumps(obj).decode("utf-8")

    def _json_loads_any(s):
        
        if isinstance(s, (bytes, bytearray)):
            return orjson.loads(s)
       
        return orjson.loads(s.encode("utf-8"))

except Exception:
    
    import json

    def _json_dumps_str(obj) -> str:
        return json.dumps(obj)

    def _json_loads_any(s):
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("utf-8")
        return json.loads(s)

load_dotenv("config.env")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL manquant dans config.env")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", 10)),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", 20)),
    pool_recycle=1800,
    json_serializer=_json_dumps_str,   
    json_deserializer=_json_loads_any, 
    future=True,
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
