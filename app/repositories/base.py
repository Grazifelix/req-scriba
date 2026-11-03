"""Utilitários comuns aos repositórios: (de)serialização JSON e montagem de SQL."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable


class BaseRepository:
    table: str = ""
    json_columns: tuple[str, ...] = ()

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- conversão -----------------------------------------------------------
    def _decode(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for col in self.json_columns:
            if col in data and isinstance(data[col], str):
                data[col] = json.loads(data[col])
        return data

    def _decode_all(self, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        return [self._decode(r) for r in rows]  # type: ignore[misc]

    def _encode(self, values: dict[str, Any]) -> dict[str, Any]:
        encoded = dict(values)
        for col in self.json_columns:
            if col in encoded and encoded[col] is not None and not isinstance(encoded[col], str):
                encoded[col] = json.dumps(encoded[col], ensure_ascii=False)
        return encoded

    # -- operações genéricas -------------------------------------------------
    def insert(self, values: dict[str, Any]) -> int:
        data = self._encode(values)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = self.conn.execute(
            f"INSERT INTO {self.table} ({cols}) VALUES ({marks})", tuple(data.values())
        )
        return int(cur.lastrowid or 0)

    def update(self, row_id: Any, values: dict[str, Any]) -> None:
        if not values:
            return
        data = self._encode(values)
        assignments = ", ".join(f"{col} = ?" for col in data)
        self.conn.execute(
            f"UPDATE {self.table} SET {assignments} WHERE id = ?", (*data.values(), row_id)
        )

    def get(self, row_id: Any) -> dict[str, Any] | None:
        row = self.conn.execute(f"SELECT * FROM {self.table} WHERE id = ?", (row_id,)).fetchone()
        return self._decode(row)

    def delete(self, row_id: Any) -> None:
        self.conn.execute(f"DELETE FROM {self.table} WHERE id = ?", (row_id,))

    def _select(self, where: str = "", params: tuple = (), order: str = "id") -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order}"
        return self._decode_all(self.conn.execute(sql, params).fetchall())


def placeholders(n: int) -> str:
    return ", ".join("?" for _ in range(n))
