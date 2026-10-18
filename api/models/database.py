import sqlite3
import config


def get_db():
    # Read-only, so a missing build fails loudly instead of sqlite creating an
    # empty database in its place.
    db = sqlite3.connect(f'file:{config.DATABASE_PATH}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db
