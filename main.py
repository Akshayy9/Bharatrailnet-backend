# main.py

import os
import asyncio
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import uvicorn
from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
    Query,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordRequestForm

from jose import jwt, JWTError
from pydantic import BaseModel

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from sqlalchemy.exc import OperationalError

# -------------------- Config --------------------

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("⚠️ WARNING: DATABASE_URL not set, using SQLite fallback")
    DATABASE_URL = "sqlite:///./railway.db"

SECRET_KEY = os.getenv("SECRET_KEY", "replace_this_with_env_secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

NETLIFY_ORIGIN = os.getenv(
    "FRONTEND_ORIGIN",
    "https://bharatrailnet.netlify.app",
)

# -------------------- SQLAlchemy --------------------

def create_engine_resilient(url: str):
    # Mitigate Render Postgres idle-SSL drops
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
    )

try:
    engine = create_engine_resilient(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    print(f"✅ Database connected: {DATABASE_URL[:30]}...")
except Exception as e:
    print(f"❌ Database connection error: {e}")
    DATABASE_URL = "sqlite:///./railway.db"
    engine = create_engine_resilient(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -------------------- Models --------------------

class Section(Base):
    __tablename__ = "sections"
    id = Column(String(10), primary_key=True, index=True)
    name = Column(String(100), nullable=False)

class UserDB(Base):
    __tablename__ = "users"
    id = Column(String(50), primary_key=True, index=True)     # employee id
    name = Column(String(100), nullable=False)
    section = Column(String(10), ForeignKey("sections.id"))
    hashed_password = Column(String(128), nullable=False)
    is_active = Column(Boolean, default=True)

class TrackSegment(Base):
    __tablename__ = "track_segments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    section_id = Column(String(10), ForeignKey("sections.id"), nullable=False)
    start_km = Column(Float, nullable=False)
    end_km = Column(Float, nullable=False)

class LiveTrainData(Base):
    __tablename__ = "live_trains"
    train_id = Column(String(20), primary_key=True)
    track_segment_id = Column(Integer, ForeignKey("track_segments.id"))
    current_km = Column(Float, default=0.0)
    last_update = Column(DateTime, server_default=func.now(), onupdate=func.now())

# -------------------- Schemas --------------------

class Token(BaseModel):
    access_token: str
    token_type: str

class UserOut(BaseModel):
    id: str
    name: str
    section: str

# -------------------- Utilities --------------------

import hashlib

def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed

def create_access_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def retry_op(fn, retries=3, delay=0.5):
    for i in range(retries):
        try:
            return fn()
        except OperationalError as e:
            if i == retries - 1:
                raise
            await asyncio.sleep(delay)

# -------------------- WebSocket Manager (minimal) --------------------

class ConnectionManager:
    def __init__(self):
        self.rooms: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room: str):
        await websocket.accept()
        self.rooms.setdefault(room, []).append(websocket)

    def disconnect(self, websocket: WebSocket, room: str):
        if room in self.rooms:
            self.rooms[room] = [ws for ws in self.rooms[room] if ws is not websocket]

    async def broadcast(self, message: dict, room: str):
        for ws in list(self.rooms.get(room, [])):
            try:
                await ws.send_json(message)
            except WebSocketDisconnect:
                self.disconnect(ws, room)

manager = ConnectionManager()

# -------------------- Lifespan --------------------

async def seed_demo(db: Session):
    # idempotent seed
    if not db.query(Section).filter(Section.id == "GZB").first():
        db.add(Section(id="GZB", name="Ghaziabad"))
    if not db.query(UserDB).filter(UserDB.id == "SK001").first():
        db.add(UserDB(
            id="SK001",
            name="Signal Keeper",
            section="GZB",
            hashed_password=hash_password("demo123")
        ))
    if not db.query(TrackSegment).first():
        db.add(TrackSegment(section_id="GZB", start_km=0.0, end_km=50.0))
    db.commit()

async def periodic_train_updates():
    while True:
        await asyncio.sleep(5)
        db = SessionLocal()
        try:
            trains = db.query(LiveTrainData).all()
            updates_by_section: Dict[str, List[dict]] = {}
            for t in trains:
                t.current_km += random.uniform(0.5, 2.0)
                seg = db.query(TrackSegment).filter(TrackSegment.id == t.track_segment_id).first()
                if seg:
                    if t.current_km > seg.end_km:
                        t.current_km = seg.start_km
                    sec = seg.section_id
                    updates_by_section.setdefault(sec, []).append({
                        "id": t.train_id,
                        "location_km": round(t.current_km, 2),
                    })
            db.commit()
            for sec, updates in updates_by_section.items():
                await manager.broadcast({
                    "type": "train_position_update",
                    "data": updates,
                    "timestamp": datetime.utcnow().isoformat()
                }, sec)
        finally:
            db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # create tables and seed with retries
    for i in range(3):
        try:
            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                await seed_demo(db)
            finally:
                db.close()
            break
        except OperationalError:
            if i == 2:
                raise
            await asyncio.sleep(1.0)
    task_updates = asyncio.create_task(periodic_train_updates())
    yield
    task_updates.cancel()
    with contextlib.suppress(Exception):
        await task_updates

# -------------------- FastAPI App --------------------

app = FastAPI(title="BharatRailNet API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[NETLIFY_ORIGIN],
    allow_credentials=True,                  # required if you send Authorization/cookies
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

@app.options("/{rest_of_path:path}")
async def preflight(rest_of_path: str):
    return Response(status_code=204)

# -------------------- Routes --------------------

@app.get("/")
async def root():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}

@app.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    def query_user():
        return db.query(UserDB).filter(UserDB.id == form_data.username).first()

    # retry around DB access to survive transient SSL drops
    user = await retry_op(query_user)

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    token = create_access_token({"sub": user.id, "section": user.section})
    return {"access_token": token, "token_type": "bearer"}

@app.websocket("/ws/{section_id}")
async def ws_section(websocket: WebSocket, section_id: str):
    await manager.connect(websocket, section_id)
    try:
        while True:
            # echo pings from client; not required for broadcast-only
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, section_id)

# -------------------- Run --------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
