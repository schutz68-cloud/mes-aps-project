from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql://postgres:parabellum@localhost/mes_aps"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)