import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, request
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

DATABASE_URL = (
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Visit(Base):
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True)
    visited_at = Column(DateTime, nullable=False)
    ip_address = Column(String(45), nullable=False)


Base.metadata.create_all(bind=engine)

app = Flask(__name__)


@app.route("/hello", methods=["GET"])
def hello():
    visited_at = datetime.utcnow()
    ip_address = request.remote_addr

    session = SessionLocal()
    try:
        visit = Visit(visited_at=visited_at, ip_address=ip_address)
        session.add(visit)
        session.commit()
    finally:
        session.close()

    return "Hello", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)