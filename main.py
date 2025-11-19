# main.py

import asyncio
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
String, create_engine, func)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from dotenv import load_dotenv
import os
import hashlib

# --- Configuration & Environment Variables ---
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("⚠️  WARNING: DATABASE_URL not set, using SQLite fallback")
    DATABASE_URL = "sqlite:///./railway.db"

SECRET_KEY = "a_very_secret_key_for_jwt"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# --- SQLAlchemy Database Setup ---
from sqlalchemy.exc import OperationalError

try:
    engine = create_engine(
        DATABASE_URL,          # Render Postgres URL (TLS enforced)
        pool_pre_ping=True,    # verifies connection before checkout
        pool_recycle=300,      # recycle connections every 5 minutes
        pool_size=5,           # adjust to your plan
        max_overflow=5
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    print(f"✅ Database connected: {DATABASE_URL[:30]}...")
except Exception as e:
    print(f"❌ Database connection error: {e}")
    DATABASE_URL = "sqlite:///./railway.db"
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- SQLAlchemy Models ---
class Section(Base):
    __tablename__ = "sections"
    id = Column(String(10), primary_key=True, index=True)
    name = Column(String(100), nullable=False)

class Station(Base):
    __tablename__ = "stations"
    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(String(10), ForeignKey("sections.id"))
    name = Column(String(100), nullable=False)
    code = Column(String(10), unique=True, index=True)
    kilometer_marker = Column(Float, nullable=False)

class TrackSegment(Base):
    __tablename__ = "track_segments"
    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(String(10), ForeignKey("sections.id"))
    name = Column(String(100))
    start_km = Column(Float)
    end_km = Column(Float)

class TrainInfo(Base):
    __tablename__ = "trains"
    id = Column(String(20), primary_key=True, index=True)
    name = Column(String(100))
    priority = Column(Integer)
    live_data = relationship("LiveTrainData", back_populates="train_info", uselist=False)

class LiveTrainData(Base):
    __tablename__ = "live_train_data"
    id = Column(Integer, primary_key=True, index=True)
    train_id = Column(String(20), ForeignKey("trains.id"), unique=True)
    track_segment_id = Column(Integer, ForeignKey("track_segments.id"))
    current_km = Column(Float)
    status = Column(String(50))
    delay_minutes = Column(Integer, default=0)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    train_info = relationship("TrainInfo", back_populates="live_data")

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=func.now())
    controller_name = Column(String(100))
    action = Column(String(50))
    details = Column(String(255))
    recommendation_matched = Column(Boolean)

class UserDB(Base):
    __tablename__ = "users"
    id = Column(String(50), primary_key=True, index=True)
    name = Column(String(100))
    hashed_password = Column(String(255))
    section_id = Column(String(10), ForeignKey("sections.id"))
    section_name = Column(String(100))

# --- Pydantic Schemas ---
class Token(BaseModel):
    access_token: str
    token_type: str

class User(BaseModel):
    id: str
    name: str
    section: str
    sectionName: str
    class Config:
        from_attributes = True

class LiveTrainResponse(BaseModel):
    id: str
    name: str
    status: str
    location_km: float
    delay_minutes: int

class StationResponse(BaseModel):
    name: str
    code: str
    kilometer_marker: float
    class Config:
        from_attributes = True

class TrackSegmentResponse(BaseModel):
    name: str
    start_km: float
    end_km: float
    class Config:
        from_attributes = True

class SectionMapResponse(BaseModel):
    stations: List[StationResponse]
    tracks: List[TrackSegmentResponse]

class AuditLogResponse(BaseModel):
    timestamp: datetime
    controller_name: str
    action: str
    details: str
    recommendation_matched: bool
    class Config:
        from_attributes = True

# --- SIMPLE PASSWORD HASHING (NO BCRYPT ISSUES) ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_password_hash(password: str) -> str:
    """Simple SHA-256 password hashing - NO BCRYPT ISSUES"""
    salt = "bharatrailnet_salt_key"
    return hashlib.sha256((password + salt).encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return get_password_hash(plain_password) == hashed_password

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = db.query(UserDB).filter(UserDB.id == username).first()
    if user is None:
        raise credentials_exception
    return User(id=user.id, name=user.name, section=user.section_id, sectionName=user.section_name)

async def get_current_user_websocket(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
    except JWTError:
        return None
    
    db = SessionLocal()
    try:
        user = db.query(UserDB).filter(UserDB.id == username).first()
        if user is None:
            return None
        return User(id=user.id, name=user.name, section=user.section_id, sectionName=user.section_name)
    finally:
        db.close()

# --- Database Initialization ---
def reset_demo_user():
    """Create demo user with simple password hashing"""
    db = SessionLocal()
    try:
        try:
            UserDB.__table__.drop(engine)
            print("🗑️  Dropped users table")
        except:
            pass
        
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created")
        
        section = db.query(Section).filter(Section.id == "GZB").first()
        if not section:
            section = Section(id="GZB", name="Ghaziabad")
            db.add(section)
            db.commit()
            print("✅ Section created: GZB")
        
        hashed = get_password_hash("demo123")
        
        new_user = UserDB(
            id="SK001",
            name="Demo Controller",
            hashed_password=hashed,
            section_id="GZB",
            section_name="Ghaziabad"
        )
        db.add(new_user)
        db.commit()
        
        test_works = verify_password("demo123", hashed)
        
        print("✅ Demo user created successfully")
        print("   Username: SK001")
        print("   Password: demo123")
        print("   Section: GZB")
        print(f"   Password test: {'PASS' if test_works else 'FAIL'}")
        
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False
    finally:
        db.close()

# --- WebSocket Manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Dict] = {}

    async def connect(self, websocket: WebSocket, section: str, user: User):
        await websocket.accept()
        self.active_connections[section] = {"websocket": websocket, "user": user}
    
    def disconnect(self, section: str):
        if section in self.active_connections:
            del self.active_connections[section]
    
    async def broadcast(self, message: dict, section: str):
        if section in self.active_connections:
            try:
                await self.active_connections[section]["websocket"].send_json(message)
            except:
                self.disconnect(section)

manager = ConnectionManager()

# --- Background Tasks ---
async def periodic_train_updates():
    while True:
        try:
            await asyncio.sleep(5)
            db = SessionLocal()
            try:
                trains_to_update = db.query(LiveTrainData).all()
                updates_by_section = {}
                
                for train in trains_to_update:
                    train.current_km += random.uniform(0.5, 2.0)
                    track = db.query(TrackSegment).filter(TrackSegment.id == train.track_segment_id).first()
                    if track:
                        if train.current_km > track.end_km:
                            train.current_km = track.start_km
                        section_id = track.section_id
                        if section_id not in updates_by_section:
                            updates_by_section[section_id] = []
                        updates_by_section[section_id].append({
                            "id": train.train_id,
                            "location_km": train.current_km
                        })
                
                db.commit()
                
                for section_id, updates in updates_by_section.items():
                    await manager.broadcast({
                        "type": "train_position_update",
                        "data": updates,
                        "timestamp": datetime.now().isoformat()
                    }, section_id)
            finally:
                db.close()
        except:
            await asyncio.sleep(10)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Starting BharatRailNet API...")
    reset_demo_user()
    task = asyncio.create_task(periodic_train_updates())
    yield
    print("🛑 Shutting down...")
    task.cancel()

# --- FastAPI App ---
app = FastAPI(title="BharatRailNet API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API Endpoints ---
@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.id == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    
    access_token = create_access_token(data={"sub": user.id})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/user/me", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.get("/api/dashboard/kpis")
async def get_kpis(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    avg_delay = db.query(func.avg(LiveTrainData.delay_minutes)).scalar() or 0
    return {"punctuality": 98.2, "average_delay": avg_delay, "section_throughput": 22, "track_utilization": 78}

@app.get("/api/dashboard/trains", response_model=List[LiveTrainResponse])
async def get_live_train_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    section_tracks = db.query(TrackSegment.id).filter(TrackSegment.section_id == user.section).subquery()
    trains = db.query(LiveTrainData).filter(LiveTrainData.track_segment_id.in_(section_tracks)).all()
    return [LiveTrainResponse(id=train.train_info.id, name=train.train_info.name, status=train.status, location_km=train.current_km, delay_minutes=train.delay_minutes) for train in trains]

@app.get("/api/section_map/{section_id}", response_model=SectionMapResponse)
async def get_section_map(section_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stations = db.query(Station).filter(Station.section_id == section_id).order_by(Station.kilometer_marker).all()
    tracks = db.query(TrackSegment).filter(TrackSegment.section_id == section_id).all()
    return {"stations": stations, "tracks": tracks}

@app.get("/api/section_map/{section_id}/geo")
async def get_section_map_geo(section_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get geographical data for railway section map with Leaflet"""
    stations = db.query(Station).filter(Station.section_id == section_id).all()
    
    # Generate coordinates for stations
    # In production, you'd have actual lat/lng in your database
    stations_geo = []
    base_lat = 28.6675  # Ghaziabad base coordinates
    base_lng = 77.4199
    
    for i, station in enumerate(stations):
        stations_geo.append({
            "name": station.name,
            "code": station.code,
            "kilometer_marker": station.kilometer_marker,
            "latitude": base_lat + (i * 0.05),  # Spread stations along route
            "longitude": base_lng + (i * 0.05)
        })
    
    # Get live trains
    section_tracks = db.query(TrackSegment.id).filter(TrackSegment.section_id == section_id).subquery()
    trains = db.query(LiveTrainData).filter(LiveTrainData.track_segment_id.in_(section_tracks)).all()
    
    trains_geo = []
    for train in trains:
        # Calculate approximate position based on current KM
        train_index = int(train.current_km / 10) if train.current_km else 0
        trains_geo.append({
            "train_id": train.train_info.id,
            "name": train.train_info.name,
            "status": train.status,
            "current_km": train.current_km,
            "delay_minutes": train.delay_minutes,
            "latitude": base_lat + (train_index * 0.05),
            "longitude": base_lng + (train_index * 0.05)
        })
    
    # Create routes connecting stations
    routes = []
    if len(stations_geo) > 1:
        route_points = [{"lat": s["latitude"], "lng": s["longitude"]} for s in stations_geo]
        routes.append({"points": route_points})
    
    return {
        "stations": stations_geo,
        "routes": routes,
        "live_trains": trains_geo
    }

@app.get("/api/audit_trail", response_model=List[AuditLogResponse])
async def get_audit_trail(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(50).all()
    return logs

@app.websocket("/ws/{section_id}")
async def websocket_endpoint(websocket: WebSocket, section_id: str, token: str = Query(None)):
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    user = await get_current_user_websocket(token)
    if not user or user.section != section_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    await manager.connect(websocket, section_id, user)
    
    try:
        await websocket.send_json({"type": "connection_established", "data": {"section": section_id, "user": user.name}})
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
            except:
                break
    except:
        manager.disconnect(section_id)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/")
async def root():
    return {"message": "BharatRailNet API", "version": "1.0.0"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
