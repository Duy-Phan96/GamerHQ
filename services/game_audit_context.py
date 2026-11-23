"""Read-only compatibility boundary for an already configured legacy primary DB.

Uses the existing tenant acceptance snapshot contract and installed DB read
session on both generations; only the verified path accessor differs. Does not
replace the installed DB module or initialize/provision anything.
"""
from contextlib import closing, contextmanager
import sqlite3


@contextmanager
def snapshot(database, denied, *, legacy_primary=False):
    with database.read_only():
        session = database._read_session.get()
        if session['connection'] is not None:
            raise ValueError('READ_SNAPSHOT_NESTING_REJECTED')
        path = database.DB_PATH if legacy_primary else database.current_path()
        if path.is_symlink() or not path.is_file():
            raise ValueError('DATABASE_IDENTITY_REJECTED')
        memory = sqlite3.connect(':memory:')
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as source:
                size = source.execute('PRAGMA page_size').fetchone()[0]
                def budget(_, remaining, total):
                    if total * size > 32 * 1024**2:
                        raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
                budget(0, 0, source.execute('PRAGMA page_count').fetchone()[0])
                source.backup(memory, pages=128, progress=budget)
            memory.row_factory = sqlite3.Row
            memory.execute('PRAGMA query_only=ON')
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                       sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE}
            def authorize(action, first, second, *_):
                permit = action in allowed or (action == sqlite3.SQLITE_PRAGMA
                    and first in {'integrity_check', 'user_version', 'query_only'} and second is None)
                if not permit:
                    denied.append(action)
                return sqlite3.SQLITE_OK if permit else sqlite3.SQLITE_DENY
            memory.set_authorizer(authorize)
            session['connection'] = memory
            memory.execute('BEGIN')
            yield memory
        finally:
            session['connection'] = None
            memory.close()
