"""Business logic for shopping cart management and discounts."""
from decimal import Decimal, ROUND_HALF_UP
from models import CartLine

CENT = Decimal('0.01')
VALID_DISCOUNT_CODES = {'WELCOME10', 'STUDENT20', 'SAVE15', 'FREESHIP'}


def _is_valid_quantity(quantity):
    return isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0


def _is_valid_sku(sku):
    return isinstance(sku, str) and bool(sku.strip())


def _round_money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class ShoppingCart:
    def __init__(self, user_id, repository):
        self.user_id = user_id
        self.repository = repository
        self.lines = []
        self.discount_code = None
        self.discounted_total = None

    # Used for Defect 7
    # Defines logic for calculating the rounded monetary subtotal across all line items.
    def _subtotal_decimal(self):
        total = sum(
            (Decimal(str(line.unit_price)) * line.quantity for line in self.lines),
            Decimal('0.00'),
        )
        return _round_money(total)

    def _calculate_total_decimal(self):
        amount = self._subtotal_decimal()
        if self.discount_code == 'WELCOME10':
            amount *= Decimal('0.9')
        elif self.discount_code == 'STUDENT20':
            amount *= Decimal('0.8')
        elif self.discount_code == 'SAVE15':
            amount = max(Decimal('0.00'), amount - Decimal('15.00'))
        elif self.discount_code == 'FREESHIP':
            pass
        return _round_money(max(Decimal('0.00'), amount))

    def _sync_discount(self):
        if self.discount_code is not None:
            self.discounted_total = float(self._calculate_total_decimal())
        else:
            self.discounted_total = None

    # Defect 4:
    # Where: add_item accepted any quantity (negative, zero, float, bool) and unchecked sku.
    # What can go wrong: Negative quantities lower totals and increase DB stock on checkout.
    # Correction: Validated non-empty sku and positive integer quantity before lookup.
    #
    # and
    # Defect 5:
    # Where: Stock check compared product.stock only against new quantity, ignoring existing cart quantity.
    # What can go wrong: Repeated add_item calls allow ordering more units than are in stock.
    # Correction: Checked existing.quantity + quantity against product.stock and refreshed product price/name.
    def add_item(self, sku, quantity):
        if not _is_valid_sku(sku) or not _is_valid_quantity(quantity):
            print('Invalid product or quantity')
            return False
        product = self.repository.get_product(sku)
        if not product:
            print('Unknown product')
            return False
        existing = next((x for x in self.lines if x.sku == sku), None)
        new_quantity = (existing.quantity if existing else 0) + quantity
        if product.stock < new_quantity:
            print('Not enough stock')
            return False
        if existing:
            existing.quantity = new_quantity
            existing.unit_price = product.price
            existing.name = product.name
        else:
            self.lines.append(CartLine(sku, product.name, product.price, quantity))
        self._sync_discount()
        return True

    # Defect 4 Continued:
    # Where: remove_item did not validate quantity when provided.
    # What can go wrong: Negative quantity increases item quantity instead of removing items.
    # Correction: Validated positive integer quantity when quantity is not None and updated discount state.
    def remove_item(self, sku, quantity=None):
        if not _is_valid_sku(sku):
            print('Item not found')
            return False
        if quantity is not None and not _is_valid_quantity(quantity):
            print('Invalid quantity')
            return False
        for line in self.lines:
            if line.sku == sku:
                if quantity is None or quantity >= line.quantity:
                    self.lines.remove(line)
                else:
                    line.quantity -= quantity
                # Assumption: emptying the cart clears any active coupon code;
                # non-empty edits keep the coupon and recalculate the discount.
                if not self.lines:
                    self.discount_code = None
                self._sync_discount()
                return True
        print('Item not found')
        return False

    # Defect 7:
    # Where: Monetary math used binary floats (e.g., 2 * 29.95 * 0.9 == 53.910000000000004).
    # What can go wrong: Floating-point drift causes inaccurate charges and rounding errors.
    # Correction: Used Decimal arithmetic rounded to 2 decimal places with ROUND_HALF_UP.
    def subtotal(self):
        return float(self._subtotal_decimal())

    # Defect 6:
    # Where: apply_discount stacked discounts on total(), cached stale discounted_total across edits,
    # and let SAVE15 produce negative totals.
    # What can go wrong: Users can stack coupons, edit cart without updating total, or get negative totals.
    # Correction: Computed total() dynamically from subtotal() and a single discount_code, clamped >= 0.00.
    def total(self):
        return float(self._calculate_total_decimal())

    def apply_discount(self, code):
        if not isinstance(code, str) or code not in VALID_DISCOUNT_CODES:
            print('Invalid coupon')
            return False
        if code == 'FREESHIP':
            print('Shipping is free')
        self.discount_code = code
        self._sync_discount()
        return True

    # Defect 5 Continued:
    # Where: load() appended saved items onto self.lines without clearing existing lines first.
    # What can go wrong: Calling load() more than once duplicates item quantities in the cart.
    # Correction: Cleared in-memory lines and discount state before loading saved rows.
    def load(self):
        self.lines.clear()
        self.discount_code = None
        self.discounted_total = None

        for sku, quantity in self.repository.get_saved_cart(self.user_id):
            self.add_item(sku, quantity)

    def save(self):
        self.repository.save_cart(self.user_id, self.lines)
