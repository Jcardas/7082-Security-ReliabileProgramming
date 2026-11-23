# Defects and Corrections

## Defects Found

1. SQL injection in product and saved-cart queries (`repository.py`)
- Where: `get_product` and `get_saved_cart`
- What: The SQL queries put user input directly into f-strings, so special characters or SQL text could break the query or expose data.
- Correction: Changed both queries to use parameterized `?` placeholders.

2. Unclosed database connections and missing user filter in `list_orders` (`repository.py`)
- Where: `StoreRepository` methods and `list_orders`
- What: If an error happened before `conn.close()`, the database connection stayed open. Also, `list_orders` ignored `user_id` and returned orders for every user.
- Correction: Added a `_connection()` helper that always commits or rolls back and closes the connection. Added `WHERE user_id = ?` to `list_orders`.

3. Stock could go negative (`repository.py`)
- Where: `update_stock`
- What: Stock was subtracted without checking if `quantity` was positive or if enough stock was left, so stock could go below zero.
- Correction: Required `quantity` to be a positive integer, added `AND stock >= ?` to the SQL update, and returned `True` or `False` based on whether a row updated.

4. Invalid quantities and SKUs allowed in the cart (`cart.py`)
- Where: `add_item` and `remove_item`
- What: Negative numbers, zero, booleans, floats, and blank SKUs were accepted. Negative quantities could lower totals, increase cart amounts on removal, or add stock back at checkout.
- Correction: Added checks so SKUs must be non-empty strings and quantities must be positive integers.

5. Cart allowed exceeding stock and `load()` duplicated items (`cart.py`)
- Where: `add_item` and `load`
- What: Calling `add_item` twice for the same product only checked the new quantity instead of `existing.quantity + quantity`. Calling `load()` twice added saved items onto the existing cart and doubled quantities.
- Correction: Checked `existing.quantity + quantity` against available stock and refreshed the item price and name. Cleared `self.lines` and discount state before loading in `load()`.

6. Discounts stacked, stale totals stayed after cart edits, and `SAVE15` went negative (`cart.py`)
- Where: `apply_discount`, `total`, `add_item`, and `remove_item`
- What: `apply_discount` used `total()` instead of `subtotal()`, allowing coupons to stack. `discounted_total` was cached once and went stale when items changed. `SAVE15` could also make totals negative.
- Correction: Made `total()` recalculate from `subtotal()` and the single active `discount_code`, clamped totals at `0.00` or higher, kept `discounted_total` updated on edits, and cleared `discount_code` when the cart is empty.

7. Floating-point rounding errors in money math (`cart.py`)
- Where: `subtotal` and `total`
- What: Normal float math caused rounding errors like `53.910000000000004`.
- Correction: Used `Decimal` math rounded to 2 decimal places with `ROUND_HALF_UP`.

8. Stock taken before payment and cart wiped on failed payment (`checkout.py`, `repository.py`)
- Where: `checkout`
- What: Stock was subtracted before charging payment, and the cart was cleared even when payment was declined or threw an error. Checkout also did not re-check current database stock or prices first.
- Correction: Checked stock and refreshed prices from the database before charging. Kept stock and the cart unchanged on declined or failed payments. On approved payments, updated stock, saved the order, and cleared the saved cart together in one transaction.

9. Duplicate checkouts with the same `request_id` and missing input checks (`checkout.py`, `repository.py`, `setup_db.py`)
- Where: `checkout`, `save_order`, and the `orders` table in `setup_db.py`
- What: Repeating checkout with the same `request_id` charged twice and created duplicate orders. Blank `user_id`, `payment_token`, or `request_id` values were also accepted.
- Correction: Added a unique index on `(user_id, request_id)`, rejected blank inputs with `invalid_request`, and returned the existing order when the same `request_id` is sent again.

10. Raw payment token printed to output (`checkout.py`)
- Where: `checkout`
- What: The full `payment_token` was printed in a log message.
- Correction: Removed `payment_token` from the printed output.

## Assumptions
- Each `request_id` is used once per user. Repeating the same `request_id` returns the previous result without charging again, so retrying after a declined payment needs a new `request_id`.
- Only one discount code can be active at a time. Editing a non-empty cart keeps the code and recalculates the total, while emptying the cart clears it.

## Test Results
- Local run (`python3 -m pytest -q`): `18 passed in 0.07s` (2 in `tests/test_smoke.py`, 8 in `tests/test_cart.py`, 8 in `tests/test_checkout.py`)
- GitHub Actions (`.github/workflows/tests.yml` on `macos-latest`): Passed (18 passed)
