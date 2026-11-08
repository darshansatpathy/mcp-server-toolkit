"""The example domain: a tenant-scoped support-ticket store on SQLite.

Every query takes the tenant; a ticket in another tenant is indistinguishable from one that does
not exist. Writes that agents may retry take an idempotency key.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio.to_thread

SCHEMA = """
create table if not exists tickets (
  seq integer primary key autoincrement,
  id text unique not null,
  tenant text not null,
  title text not null,
  body text not null,
  status text not null default 'open',
  priority text not null,
  resolution text,
  created_by text not null,
  created_at real not null,
  updated_at real not null
);
create index if not exists tickets_tenant on tickets(tenant, seq);
create table if not exists comments (
  id integer primary key autoincrement,
  ticket_id text not null references tickets(id),
  tenant text not null,
  author text not null,
  body text not null,
  created_at real not null
);
create table if not exists idempotency (
  tenant text not null,
  key text not null,
  ticket_id text not null,
  primary key (tenant, key)
);
create virtual table if not exists tickets_fts using fts5(title, body, content='tickets', content_rowid='seq');
create trigger if not exists tickets_ai after insert on tickets begin
  insert into tickets_fts(rowid, title, body) values (new.seq, new.title, new.body);
end;
"""


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


@dataclass
class Store:
    path: Path | str

    def __post_init__(self) -> None:
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("pragma journal_mode=wal")
        self._conn.execute("pragma foreign_keys=on")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _run(self, fn: Any, *args: Any) -> Any:
        with self._lock:
            return fn(*args)

    async def call(self, fn: Any, *args: Any) -> Any:
        """Run a query in a worker thread. On timeout the caller stops waiting at once; the query
        finishes in its thread, and writes are single transactions, so it never leaves half a write."""
        return await anyio.to_thread.run_sync(self._run, fn, *args, abandon_on_cancel=True)

    def close(self) -> None:
        self._conn.close()

    # --- queries (run in a worker thread through `call`) ---

    def ping(self) -> bool:
        return bool(self._conn.execute("select 1").fetchone()[0] == 1)

    def search(
        self, tenant: str, query: str, status: str | None, priority: str | None, limit: int, before: int | None
    ) -> list[dict[str, Any]]:
        sql = ["select t.* from tickets t"]
        args: list[Any] = []
        if query.strip():
            sql.append("join tickets_fts f on f.rowid = t.seq and tickets_fts match ?")
            args.append(_fts_query(query))
        sql.append("where t.tenant = ?")
        args.append(tenant)
        if status:
            sql.append("and t.status = ?")
            args.append(status)
        if priority:
            sql.append("and t.priority = ?")
            args.append(priority)
        if before:
            sql.append("and t.seq < ?")
            args.append(before)
        sql.append("order by t.seq desc limit ?")
        args.append(limit)
        return [dict(r) for r in self._conn.execute(" ".join(sql), args)]

    def get(self, tenant: str, ticket_id: str) -> dict[str, Any]:
        row = self._conn.execute("select * from tickets where tenant = ? and id = ?", (tenant, ticket_id)).fetchone()
        if row is None:
            raise NotFound(ticket_id)
        t = dict(row)
        t["comments"] = [
            dict(c)
            for c in self._conn.execute(
                "select id, author, body, created_at from comments where tenant = ? and ticket_id = ? order by id", (tenant, ticket_id)
            )
        ]
        return t

    def create(self, tenant: str, author: str, title: str, body: str, priority: str, key: str | None) -> tuple[dict[str, Any], bool]:
        c = self._conn
        c.execute("begin immediate")
        try:
            if key:
                row = c.execute("select ticket_id from idempotency where tenant = ? and key = ?", (tenant, key)).fetchone()
                if row:
                    c.execute("commit")
                    return self.get(tenant, row[0]), False
            now = time.time()
            cur = c.execute(
                "insert into tickets (id, tenant, title, body, priority, created_by, created_at, updated_at) values ('pending', ?, ?, ?, ?, ?, ?, ?)",
                (tenant, title, body, priority, author, now, now),
            )
            tid = f"T-{cur.lastrowid:06d}"
            c.execute("update tickets set id = ? where seq = ?", (tid, cur.lastrowid))
            if key:
                c.execute("insert into idempotency (tenant, key, ticket_id) values (?, ?, ?)", (tenant, key, tid))
            c.execute("commit")
        except BaseException:
            c.execute("rollback")
            raise
        return self.get(tenant, tid), True

    def comment(self, tenant: str, author: str, ticket_id: str, body: str) -> dict[str, Any]:
        t = self.get(tenant, ticket_id)
        if t["status"] == "closed":
            raise Conflict(f"{ticket_id} is closed")
        now = time.time()
        cur = self._conn.execute(
            "insert into comments (ticket_id, tenant, author, body, created_at) values (?, ?, ?, ?, ?)",
            (ticket_id, tenant, author, body, now),
        )
        self._conn.execute("update tickets set updated_at = ? where id = ?", (now, ticket_id))
        return {"id": cur.lastrowid, "author": author, "body": body, "created_at": now}

    def close_ticket(self, tenant: str, author: str, ticket_id: str, resolution: str, note: str) -> dict[str, Any]:
        t = self.get(tenant, ticket_id)
        if t["status"] == "closed":
            if t["resolution"] == resolution:
                return t  # closing twice with the same resolution is a no-op, so retries are safe
            raise Conflict(f"{ticket_id} is already closed as {t['resolution']}")
        now = time.time()
        self._conn.execute(
            "update tickets set status = 'closed', resolution = ?, updated_at = ? where id = ?", (resolution, now, ticket_id)
        )
        if note:
            self._conn.execute(
                "insert into comments (ticket_id, tenant, author, body, created_at) values (?, ?, ?, ?, ?)",
                (ticket_id, tenant, author, note, now),
            )
        return self.get(tenant, ticket_id)

    def stats(self, tenant: str) -> dict[str, int]:
        rows = self._conn.execute("select status, count(*) from tickets where tenant = ? group by status", (tenant,))
        return {r[0]: r[1] for r in rows}


def _fts_query(text: str) -> str:
    """Quote every word, so user text can never be FTS5 syntax (no column filters, NEAR, or errors)."""
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in text).split() if w]
    return " ".join(f'"{w}"' for w in words[:16]) or '""'
