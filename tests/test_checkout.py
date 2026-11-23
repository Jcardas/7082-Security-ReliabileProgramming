"""Unit tests for checkout flow, stock safety, payment failures, and idempotency."""
from setup_db import initialize
from repository import StoreRepository
from cart import ShoppingCart
from payments import FakePaymentGateway
from checkout import CheckoutService


def make_repo(tmp_path):
    db = tmp_path / 'test.db'
    initialize(str(db))
    return StoreRepository(str(db))


class _ErrorPaymentGateway:
    def charge(self, amount, payment_token):
        raise RuntimeError('Payment service unavailable')


# Empty carts and blank request inputs should return early without charging anything.
def test_checkout_empty_cart_and_invalid_request_inputs(tmp_path):
    repo = make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)
    service = CheckoutService(repo, gateway)
    cart = ShoppingCart('alice', repo)

    empty_res = service.checkout(cart, 'TOK_1', 'req-empty')
    assert empty_res.status == 'empty'
    assert empty_res.order_id is None
    assert empty_res.total == 0.0

    cart.add_item('USB-01', 1)
    assert service.checkout(cart, '', 'req-1').status == 'invalid_request'
    assert service.checkout(cart, 'TOK_1', '  ').status == 'invalid_request'
    assert service.checkout(ShoppingCart('', repo), 'TOK_1', 'req-1').status == 'invalid_request'
    assert gateway.calls == []


# A normal approved checkout should charge the right amount, lower stock, save the order,
# and clear both the in-memory cart and saved cart.
def test_checkout_success_charges_reduces_stock_and_clears_cart(tmp_path):
    repo = make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)
    service = CheckoutService(repo, gateway)
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 2)
    cart.apply_discount('WELCOME10')
    cart.save()

    result = service.checkout(cart, 'TOK_OK', 'req-pay-1')

    assert result.status == 'paid'
    assert result.order_id is not None
    assert result.total == 53.91
    assert gateway.calls == [(53.91, 'TOK_OK')]
    assert repo.get_product('MOU-01').stock == 18
    assert cart.lines == []
    assert cart.discount_code is None
    assert repo.get_saved_cart('alice') == []


# When a card is declined, we must not take stock out or erase the customer's cart.
def test_checkout_declined_keeps_stock_and_cart_intact(tmp_path):
    repo = make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=False)
    service = CheckoutService(repo, gateway)
    cart = ShoppingCart('alice', repo)
    cart.add_item('LAP-01', 1)
    cart.save()

    result = service.checkout(cart, 'TOK_DECLINED', 'req-dec-1')

    assert result.status == 'declined'
    assert result.order_id is not None
    assert repo.get_product('LAP-01').stock == 5
    assert len(cart.lines) == 1
    assert len(repo.get_saved_cart('alice')) == 1


# If the payment gateway crashes, the order is marked payment_error while stock and cart stay safe.
def test_checkout_payment_error_keeps_stock_and_cart_intact(tmp_path):
    repo = make_repo(tmp_path)
    service = CheckoutService(repo, _ErrorPaymentGateway())
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 2)

    result = service.checkout(cart, 'TOK_ERR', 'req-err-1')

    assert result.status == 'payment_error'
    assert result.order_id is not None
    assert repo.get_product('USB-01').stock == 50
    assert len(cart.lines) == 1


# If another buyer takes stock before checkout runs, stop before calling the payment gateway.
def test_checkout_fails_when_stock_drops_before_checkout(tmp_path):
    repo = make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)
    service = CheckoutService(repo, gateway)
    cart = ShoppingCart('alice', repo)
    cart.add_item('LAP-01', 4)

    assert repo.update_stock('LAP-01', 2) is True  # 3 left in stock

    result = service.checkout(cart, 'TOK_OK', 'req-oos-1')

    assert result.status == 'out_of_stock'
    assert result.order_id is None
    assert gateway.calls == []
    assert repo.get_product('LAP-01').stock == 3
    assert len(cart.lines) == 1


# Checkout should look up the latest product price in the database so old cart prices are not used.
def test_checkout_refreshes_stale_cart_price_before_charging(tmp_path):
    repo = make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)
    service = CheckoutService(repo, gateway)
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 1)  # Initially 9.99

    with repo._connection() as conn:
        conn.execute("UPDATE products SET price = ? WHERE sku = ?", (12.50, 'USB-01'))

    result = service.checkout(cart, 'TOK_OK', 'req-price-1')

    assert result.status == 'paid'
    assert result.total == 12.50
    assert gateway.calls == [(12.50, 'TOK_OK')]


# Sending the same request_id again should return the first order without charging twice.
# Using a new request_id after a decline should allow a fresh checkout attempt.
def test_repeated_checkout_with_same_request_id_is_idempotent(tmp_path):
    repo = make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)
    service = CheckoutService(repo, gateway)
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 1)

    first = service.checkout(cart, 'TOK_OK', 'req-repeat-1')
    second = service.checkout(cart, 'TOK_OK', 'req-repeat-1')

    assert first == second
    assert len(gateway.calls) == 1
    assert repo.get_product('MOU-01').stock == 19

    cart2 = ShoppingCart('bob', repo)
    cart2.add_item('USB-01', 1)
    declined_svc = CheckoutService(repo, FakePaymentGateway(should_approve=False))
    dec = declined_svc.checkout(cart2, 'TOK_BAD', 'req-bob-1')
    assert dec.status == 'declined'

    retry = service.checkout(cart2, 'TOK_GOOD', 'req-bob-2')
    assert retry.status == 'paid'
    assert repo.get_product('USB-01').stock == 49


# Secret payment tokens must not be printed to stdout, and stock updates cannot go below zero.
def test_checkout_hides_payment_token_and_stock_cannot_go_negative(tmp_path, capsys):
    repo = make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)
    service = CheckoutService(repo, gateway)
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 1)

    service.checkout(cart, 'SECRET_CARD_TOKEN_999', 'req-log-1')
    captured = capsys.readouterr()
    assert 'SECRET_CARD_TOKEN_999' not in captured.out

    assert repo.update_stock('LAP-01', -2) is False
    assert repo.update_stock('LAP-01', 99) is False
    assert repo.get_product('LAP-01').stock == 5
