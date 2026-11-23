"""SQLite persistence with parameterized queries, transactions, and isolation."""
from contextlib import closing, contextmanager
import sqlite3
from models import Product


class _InsufficientStockError(Exception):
    """Internal signal used to roll back stock updates on conflict."""


class StoreRepository:
    def __init__(self, db_path='shop.db'):
        self.db_path = db_path

    def connect(self):
        return sqlite3.connect(self.db_path, timeout=2)

    # Defect 2:
    # Where: All repository methods called connect() and close() without try/finally.
    # What can go wrong: An SQL error skips conn.close(), leaking database connections and locks.
    # Correction: Added connection() context manager to commit and rollback transactions and always close connections.
    @contextmanager
    def _connection(self):
        with closing(self.connect()) as conn:
            with conn:
                yield conn

    # Defect 1:
    # Where: SQL query built with an f-string using sku.
    # What can go wrong: Unsanitized input can cause SQL injection or syntax errors.
    # Correction: Replaced string interpolation with a parameterized '?' query.
    def get_product(self, sku):
        with self._connection() as conn:
            row = conn.execute(
                'SELECT sku, name, price, stock FROM products WHERE sku = ?',
                (sku,),
            ).fetchone()
        return Product(*row) if row else None

    # Continued defect 1:
    # Where: SQL query built with an f-string using user_id.
    # What can go wrong: Malicious user_id input can cause SQL injection and read other users' carts.
    # Correction: Replaced string interpolation with a parameterized '?' query.
    def get_saved_cart(self, user_id):
        with self._connection() as conn:
            rows = conn.execute(
                'SELECT sku, quantity FROM saved_cart WHERE user_id = ?',
                (user_id,),
            ).fetchall()
        return rows

    def save_cart(self, user_id, lines):
        with self._connection() as conn:
            conn.execute('DELETE FROM saved_cart WHERE user_id = ?', (user_id,))
            for line in lines:
                conn.execute(
                    'INSERT INTO saved_cart(user_id, sku, quantity) VALUES (?, ?, ?)',
                    (user_id, line.sku, line.quantity),
                )

    # Defect 3:
    # Where: update_stock subtracted quantity without validating quantity or checking stock >= quantity.
    # What can go wrong: Negative quantities inflate stock and concurrent/stale orders drive stock negative.
    # Correction: Rejected non-positive quantities, added 'AND stock >= ?' guard, and returned whether update succeeded.
    def update_stock(self, sku, quantity):
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            return False
        with self._connection() as conn:
            cur = conn.execute(
                'UPDATE products SET stock = stock - ? WHERE sku = ? AND stock >= ?',
                (quantity, sku, quantity),
            )
            return cur.rowcount == 1

    # Defect 9 (repository.py - idempotent order lookup and atomic finalization):
    # Where: save_order inserted new rows blindly with no request_id lookup or stock transaction.
    # What can go wrong: Repeated checkout requests create duplicate orders and partial stock updates.
    # Correction: Added get_order_by_request_id, idempotent save_order, and atomic finalize_paid_order.
    def get_order_by_request_id(self, user_id, request_id):
        with self._connection() as conn:
            return conn.execute(
                'SELECT id, status, amount FROM orders WHERE user_id = ? AND request_id = ?',
                (user_id, request_id),
            ).fetchone()

    def save_order(self, user_id, amount, status, request_id):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute(
                'SELECT id FROM orders WHERE user_id = ? AND request_id = ?',
                (user_id, request_id),
            ).fetchone()
            if existing:
                return existing[0]
            cur = conn.execute(
                'INSERT INTO orders(user_id, amount, status, request_id) VALUES (?, ?, ?, ?)',
                (user_id, amount, status, request_id),
            )
            return cur.lastrowid

    def finalize_paid_order(self, user_id, lines, amount, request_id):
        required_by_sku = {}
        for line in lines:
            required_by_sku[line.sku] = required_by_sku.get(line.sku, 0) + line.quantity

        try:
            with self._connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                existing = conn.execute(
                    'SELECT id, status, amount FROM orders WHERE user_id = ? AND request_id = ?',
                    (user_id, request_id),
                ).fetchone()
                if existing:
                    return existing

                for sku, qty in required_by_sku.items():
                    if qty <= 0:
                        raise _InsufficientStockError()
                    cur = conn.execute(
                        'UPDATE products SET stock = stock - ? WHERE sku = ? AND stock >= ?',
                        (qty, sku, qty),
                    )
                    if cur.rowcount != 1:
                        raise _InsufficientStockError()

                cur = conn.execute(
                    'INSERT INTO orders(user_id, amount, status, request_id) VALUES (?, ?, ?, ?)',
                    (user_id, amount, 'paid', request_id),
                )
                conn.execute('DELETE FROM saved_cart WHERE user_id = ?', (user_id,))
                return cur.lastrowid, 'paid', amount
        except _InsufficientStockError:
            return None, 'out_of_stock', 0.0

    # Defect 2 Continued:
    # Where: list_orders ignored the user_id argument and selected all rows from orders.
    # What can go wrong: Any user can view every other user's order history.
    # Correction: Added 'WHERE user_id = ?' filter using parameterized query.
    def list_orders(self, user_id):
        with self._connection() as conn:
            rows = conn.execute(
                'SELECT id, user_id, amount, status FROM orders WHERE user_id = ? ORDER BY id DESC',
                (user_id,),
            ).fetchall()
        return rows
