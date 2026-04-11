"""Reset the users table to current schema. Drops and recreates."""
from models import Base, engine
from sqlalchemy import text

with engine.connect() as c:
    c.execute(text("DROP TABLE IF EXISTS users"))
    c.commit()
Base.metadata.create_all(bind=engine)
print("Users table reset to current schema")
