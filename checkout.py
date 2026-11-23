"""Checkout coordinator with input validation, idempotency, and atomic stock updates."""
from models import OrderResult


def _is_non_empty_str(value):
    return isinstance(value, str) and bool(value.strip())


class CheckoutService:
    def __init__(self, repository, payment_gateway):
        self.repository = repository
        self.payment_gateway = payment_gateway

    def checkout(self, cart, payment_token, request_id):
        # Defect 9:
        # Where: checkout() only warned on missing request_id for orders > 1000 and never checked duplicates.
        # What can go wrong: Missing identifiers or repeated checkout calls cause duplicate charges and orders.
        # Correction: Required non-empty user_id, payment_token, and request_id, and returned any existing
        #             order for the same (user_id, request_id) before checking cart or charging.
        # Assumption: Each request_id is a single-use idempotency key per user; retrying after a decline
        #             requires a new request_id.
        if not _is_non_empty_str(cart.user_id) or not _is_non_empty_str(payment_token) or not _is_non_empty_str(request_id):
            return OrderResult(None, 'invalid_request', 0.0)

        existing = self.repository.get_order_by_request_id(cart.user_id, request_id)
        if existing:
            order_id, status, amount = existing
            return OrderResult(order_id, status, amount)

        if len(cart.lines) == 0:
            return OrderResult(None, 'empty', 0.0)

        # Defect 8:
        # Where: checkout() decremented stock before charging and never checked current
        # DB stock or prices.
        # What can go wrong: Out-of-stock or deleted items are charged, stock goes
        # negative, and stale prices persist.
        # Correction: Verified product existence, positive quantities, and stock
        # availability before charging,
        #             and refreshed cart line prices/names from the repository.
        products_by_sku = {}
        required_by_sku = {}
        for line in cart.lines:
            if not isinstance(line.quantity, int) or isinstance(line.quantity, bool) or line.quantity <= 0:
                return OrderResult(None, 'invalid_cart', 0.0)
            product = products_by_sku.get(line.sku) or self.repository.get_product(line.sku)
            if not product:
                return OrderResult(None, 'out_of_stock', 0.0)
            products_by_sku[line.sku] = product
            line.unit_price = product.price
            line.name = product.name
            required_by_sku[line.sku] = required_by_sku.get(line.sku, 0) + line.quantity

        for sku, needed in required_by_sku.items():
            if products_by_sku[sku].stock < needed:
                return OrderResult(None, 'out_of_stock', 0.0)

        cart._sync_discount()
        amount = cart.total()

        # Defect 10:
        # Where: print(f'Charging payment token: {payment_token}') logged the raw payment token.
        # What can go wrong: Sensitive payment credentials leak into stdout/log files.
        # Correction: Removed the raw payment_token from console output.
        print('Processing payment')
        if amount > 1000:
            print('Large order')

        try:
            response = self.payment_gateway.charge(amount, payment_token)
            approved = bool(isinstance(response, dict) and response.get('approved'))
            status = 'paid' if approved else 'declined'
        except Exception:
            status = 'payment_error'

        # Defect 8 Continued:
        # Where: Stock was updated before payment, and cart was cleared even
        # on declined/failed payments.
        # What can go wrong: Declined or errored payments permanently lose stock
        # and wipe the user's cart.
        # Correction: On decline/error, recorded the order status without changing
        # stock or clearing the cart. On approval, atomically decremented stock,
        # saved the order, and cleared the cart.
        if status != 'paid':
            order_id = self.repository.save_order(cart.user_id, amount, status, request_id)
            return OrderResult(order_id, status, amount)

        order_id, final_status, final_amount = self.repository.finalize_paid_order(
            cart.user_id, cart.lines, amount, request_id
        )
        if final_status == 'paid':
            cart.lines.clear()
            cart.discount_code = None
            cart.discounted_total = None
        return OrderResult(order_id, final_status, final_amount)
