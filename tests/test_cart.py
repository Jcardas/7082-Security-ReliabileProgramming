"""Unit tests for cart behavior, discounts, saved carts, and repository lookups."""
from setup_db import initialize
from repository import StoreRepository
from cart import ShoppingCart


def make_repo(tmp_path):
    db = tmp_path / 'test.db'
    initialize(str(db))
    return StoreRepository(str(db))


# Make sure bad numbers, booleans, and unknown SKUs cannot be added to the cart.
def test_add_item_rejects_invalid_quantities_and_skus(tmp_path):
    repo = make_repo(tmp_path)
    cart = ShoppingCart('alice', repo)

    assert cart.add_item('LAP-01', 0) is False
    assert cart.add_item('LAP-01', -3) is False
    assert cart.add_item('LAP-01', True) is False
    assert cart.add_item('LAP-01', 1.5) is False
    assert cart.add_item('', 1) is False
    assert cart.add_item('NOPE-99', 1) is False
    assert cart.lines == []


# Adding the same product more than once should combine quantities and still respect stock.
# Here LAP-01 only has 5 in stock, so 3 + 3 must fail while 3 + 2 succeeds.
def test_add_duplicate_product_checks_combined_stock(tmp_path):
    repo = make_repo(tmp_path)
    cart = ShoppingCart('alice', repo)

    assert cart.add_item('LAP-01', 3) is True
    assert cart.add_item('LAP-01', 3) is False
    assert cart.lines[0].quantity == 3

    assert cart.add_item('LAP-01', 2) is True
    assert len(cart.lines) == 1
    assert cart.lines[0].quantity == 5


# Check partial removal, full removal, missing items, and rejecting negative remove counts.
def test_remove_item_partial_full_and_invalid_quantity(tmp_path):
    repo = make_repo(tmp_path)
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 3)

    assert cart.remove_item('USB-01', -1) is False
    assert cart.remove_item('USB-01', 0) is False
    assert cart.lines[0].quantity == 3

    assert cart.remove_item('USB-01', 1) is True
    assert cart.lines[0].quantity == 2

    assert cart.remove_item('USB-01') is True
    assert cart.lines == []
    assert cart.remove_item('USB-01') is False


# Prices should be rounded cleanly to two decimal places without floating-point drift.
def test_subtotal_and_discount_decimal_precision(tmp_path):
    repo = make_repo(tmp_path)
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 2)  # 2 * 29.95 = 59.90

    assert cart.subtotal() == 59.90
    assert cart.apply_discount('WELCOME10') is True
    assert cart.total() == 53.91

    assert cart.apply_discount('STUDENT20') is True
    assert cart.total() == 47.92


# Applying a coupon twice should not stack, and editing the cart later must update the total.
# Also, removing the last item should clear the active discount code.
def test_discount_does_not_stack_and_updates_when_cart_changes(tmp_path):
    repo = make_repo(tmp_path)
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 2)  # 59.90

    assert cart.apply_discount('WELCOME10') is True
    assert cart.apply_discount('WELCOME10') is True
    assert cart.total() == 53.91

    cart.add_item('USB-01', 1)  # 59.90 + 9.99 = 69.89 -> 62.90
    assert cart.total() == 62.90
    assert cart.discounted_total == 62.90

    cart.remove_item('MOU-01')
    cart.remove_item('USB-01')
    assert cart.total() == 0.0
    assert cart.discount_code is None


# Fixed-dollar discounts should stop at zero instead of going negative, and unknown codes must fail.
def test_save15_clamps_total_at_zero_and_rejects_invalid_coupon(tmp_path):
    repo = make_repo(tmp_path)
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 1)  # 9.99

    assert cart.apply_discount('SAVE15') is True
    assert cart.total() == 0.0

    assert cart.apply_discount('FREESHIP') is True
    assert cart.total() == 9.99

    assert cart.apply_discount('INVALID_CODE') is False
    assert cart.discount_code == 'FREESHIP'


# Saving and loading a cart should keep the same items even if load() is called twice in a row.
def test_saved_cart_persists_and_repeated_load_does_not_duplicate(tmp_path):
    repo = make_repo(tmp_path)
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 2)
    cart.save()

    reloaded = ShoppingCart('alice', repo)
    reloaded.load()
    reloaded.load()

    assert len(reloaded.lines) == 1
    assert reloaded.lines[0].sku == 'USB-01'
    assert reloaded.lines[0].quantity == 2


# Check that SQL injection strings do not return data and that users only see their own orders.
def test_repository_queries_prevent_sql_injection_and_isolate_user_orders(tmp_path):
    repo = make_repo(tmp_path)
    assert repo.get_product("' OR '1'='1") is None
    assert repo.get_saved_cart("' OR '1'='1") == []

    repo.save_order('alice', 29.95, 'paid', 'req-a1')
    repo.save_order('bob', 9.99, 'paid', 'req-b1')

    alice_orders = repo.list_orders('alice')
    bob_orders = repo.list_orders('bob')
    assert len(alice_orders) == 1 and alice_orders[0][1] == 'alice'
    assert len(bob_orders) == 1 and bob_orders[0][1] == 'bob'
