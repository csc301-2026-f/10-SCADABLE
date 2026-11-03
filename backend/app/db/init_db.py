from sqlalchemy.orm import Session

from app.db.database import Base, engine
from app.db.repository import ensure_overall_swot_state
import app.db.models


def init_db():
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        ensure_overall_swot_state(db)
        db.commit()
    print("Database tables initialized successfully.")


if __name__ == "__main__":
    init_db()