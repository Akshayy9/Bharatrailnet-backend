# create_sample_data.py
# Run this script to populate your database with sample data

import os
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext

# Import your models (make sure main.py is in the same directory)
from main import Base, Section, Station, TrackSegment, TrainInfo, LiveTrainData, UserDB, AuditLog

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:akshayyy@localhost/bharatrailnet")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_sample_data():
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # Clear existing data (optional - remove if you want to keep existing data)
        db.query(LiveTrainData).delete()
        db.query(TrainInfo).delete()
        db.query(AuditLog).delete()
        db.query(UserDB).delete()
        db.query(Station).delete()
        db.query(TrackSegment).delete()
        db.query(Section).delete()
        
        # 1. Create Section
        section = Section(id="GZB", name="Ghaziabad")
        db.add(section)
        
        # 2. Create Track Segments
        track1 = TrackSegment(id=1, section_id="GZB", name="GZB-ALJN Line 1", start_km=0.0, end_km=45.0)
        track2 = TrackSegment(id=2, section_id="GZB", name="GZB-ALJN Line 2", start_km=0.0, end_km=45.0)
        track3 = TrackSegment(id=3, section_id="GZB", name="GZB-ALJN Line 3", start_km=0.0, end_km=45.0)
        db.add_all([track1, track2, track3])
        
        # 3. Create Stations
        stations = [
            Station(section_id="GZB", name="Ghaziabad", code="GZB", kilometer_marker=0.0),
            Station(section_id="GZB", name="Sikandarpur", code="SKQ", kilometer_marker=8.5),
            Station(section_id="GZB", name="Dankaur", code="DKDE", kilometer_marker=15.2),
            Station(section_id="GZB", name="Chola", code="CHL", kilometer_marker=22.8),
            Station(section_id="GZB", name="Atrauli Junction", code="ALJN", kilometer_marker=45.0),
        ]
        db.add_all(stations)
        
        # 4. Create User
        hashed_password = pwd_context.hash("demo123")
        user = UserDB(
            id="demo", 
            name="Demo Controller", 
            hashed_password=hashed_password,
            section_id="GZB", 
            section_name="Ghaziabad (GZB)"
        )
        db.add(user)
        
        # 5. Create Train Info
        trains = [
            TrainInfo(id="12878", name="Ghaziabad-Aligarh Express", priority=1),
            TrainInfo(id="04567", name="GZB-ALJN SPL", priority=2),
            TrainInfo(id="22439", name="Vande Bharat Express", priority=1),
            TrainInfo(id="18238", name="Chhatrapati Shivaji Express", priority=2),
            TrainInfo(id="12004", name="Lucknow Shatabdi", priority=1),
        ]
        db.add_all(trains)
        
        # 6. Create Live Train Data
        live_trains = [
            LiveTrainData(train_id="12878", track_segment_id=1, current_km=12.5, status="On Time", delay_minutes=0),
            LiveTrainData(train_id="04567", track_segment_id=2, current_km=8.2, status="Delayed", delay_minutes=5),
            LiveTrainData(train_id="22439", track_segment_id=1, current_km=28.7, status="On Time", delay_minutes=0),
            LiveTrainData(train_id="18238", track_segment_id=3, current_km=35.1, status="Conflict", delay_minutes=12),
            LiveTrainData(train_id="12004", track_segment_id=2, current_km=18.9, status="On Time", delay_minutes=0),
        ]
        db.add_all(live_trains)
        
        # 7. Create Sample Audit Logs
        audit_logs = [
            AuditLog(
                timestamp=datetime.now(),
                controller_name="Demo Controller",
                action="Accepted",
                details="Halted Train 04567 at Sikandarpur for 5 minutes",
                recommendation_matched=True
            ),
            AuditLog(
                timestamp=datetime.now(),
                controller_name="Demo Controller", 
                action="Override",
                details="Manually rerouted Train 12878 via alternate route",
                recommendation_matched=False
            ),
        ]
        db.add_all(audit_logs)
        
        # Commit all changes
        db.commit()
        print("✅ Sample data created successfully!")
        print("User credentials:")
        print("  Username: demo")
        print("  Password: demo123")
        print("  Section: GZB (Ghaziabad)")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error creating sample data: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    create_sample_data()