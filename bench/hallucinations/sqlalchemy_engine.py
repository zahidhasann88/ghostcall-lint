"""SQLAlchemy 2.0 removed ``Engine.execute`` -- the most common 1.4 habit."""

import sqlalchemy

engine = sqlalchemy.create_engine("sqlite://")
# ghostcall-expect: GC003 7 medium
engine.execute("select 1")
# ghostcall-expect: GC003 9
handler = sqlalchemy.engine.Engine.execute
print(engine, handler)
