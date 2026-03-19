from django.conf import settings

# Fallback defaults used if DB is unavailable (e.g. during migrations)
FREE_DELIVERY_LIMIT = 799
DELIVERY_CHARGE = 59


def send_order_email(order, template_name, subject):
    """Send an HTML order email via Brevo HTTP API in a background thread."""
    import threading
    from django.template.loader import render_to_string

    customer = order.customer
    if not customer or not customer.email:
        return

    items = order.items.select_related('sku__product').all()
    html = render_to_string(f'store/emails/{template_name}', {
        'order': order,
        'items': items,
        'store_name': 'Zivo',
    })
    order_id = order.id
    recipient = customer.email

    def _send():
        try:
            from brevo import Brevo, SendTransacEmailRequestToItem, SendTransacEmailRequestSender
            client = Brevo(api_key=settings.BREVO_API_KEY)
            client.transactional_emails.send_transac_email(
                to=[SendTransacEmailRequestToItem(email=recipient)],
                sender=SendTransacEmailRequestSender(
                    email=settings.DEFAULT_FROM_EMAIL, name='Zivo'
                ),
                subject=subject,
                html_content=html,
            )
            flag = 'confirmation_email_sent' if 'confirmation' in template_name else 'shipped_email_sent'
            from .models import Order
            Order.objects.filter(id=order_id).update(**{flag: True})
        except Exception as e:
            print(f"[EMAIL ERROR] Failed to send {template_name} for order {order_id}: {e}")

    threading.Thread(target=_send, daemon=False).start()


# ─────────────────────────── Offer engine ────────────────────────────────────

def _get_active_offers():
    from django.db.models import Q
    from django.utils import timezone
    from .models import Offer
    today = timezone.localdate()
    return (
        Offer.objects
        .filter(is_active=True)
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
        .filter(Q(valid_to__isnull=True)   | Q(valid_to__gte=today))
        .prefetch_related('applicable_products', 'applicable_categories')
    )


def _matching_items(offer, cart_items):
    """Return cart_items that fall within the offer's scope (empty scope = all items)."""
    prod_ids = set(offer.applicable_products.values_list('id', flat=True))
    cat_ids  = set(offer.applicable_categories.values_list('id', flat=True))
    if not prod_ids and not cat_ids:
        return cart_items
    result = []
    for item in cart_items:
        sku = item['sku']
        if (prod_ids and sku.product_id in prod_ids) or \
           (cat_ids  and sku.product.category_id in cat_ids):
            result.append(item)
    return result


def _unit_prices(matching):
    """Expand items to a flat list of unit prices (int), sorted ascending."""
    units = []
    for item in matching:
        units.extend([int(item['sku'].selling_price)] * item['quantity'])
    return sorted(units)


def _calc_percentage(offer, matching):
    total = sum(int(item['sku'].selling_price) * item['quantity'] for item in matching)
    return round(total * offer.discount_percent / 100)


def _calc_bogo(matching):
    """Every 2 items: the cheaper one is free."""
    units = _unit_prices(matching)          # sorted ascending (cheapest first)
    free_count = len(units) // 2
    return sum(units[:free_count])


def _calc_buy_x_get_y(offer, matching):
    """Buy X, get Y at get_discount_percent% off. Applied in groups."""
    units = sorted(_unit_prices(matching), reverse=True)  # most expensive first
    group = offer.buy_quantity + offer.get_quantity
    discount = 0
    i = 0
    while i + group <= len(units):
        # The Y cheapest in this group get the discount
        cheapest_y = sorted(units[i:i + group])[:offer.get_quantity]
        discount += sum(p * offer.get_discount_percent // 100 for p in cheapest_y)
        i += group
    return discount


def _calc_min_qty(offer, matching):
    total_qty = sum(item['quantity'] for item in matching)
    if total_qty < offer.min_quantity:
        return 0
    total_price = sum(int(item['sku'].selling_price) * item['quantity'] for item in matching)
    return round(total_price * offer.discount_percent / 100)


def calculate_offer_discounts(cart_items):
    """
    Auto-apply all currently valid offers to the given cart items.

    cart_items: list of dicts — each with 'sku' (SKU instance) and 'quantity' (int).
    Returns:
        total_discount (int)  — total ₹ discount from all applicable offers
        applied_offers (list) — [{'name': str, 'label': str, 'discount': int}, ...]
    """
    try:
        offers = list(_get_active_offers())
    except Exception:
        return 0, []

    applied = []
    total   = 0

    for offer in offers:
        matching = _matching_items(offer, cart_items)
        if not matching:
            continue
        t = offer.offer_type
        if t == 'PERCENTAGE':
            d = _calc_percentage(offer, matching)
        elif t == 'BOGO':
            d = _calc_bogo(matching)
        elif t == 'BUY_X_GET_Y':
            d = _calc_buy_x_get_y(offer, matching)
        elif t == 'MIN_QTY':
            d = _calc_min_qty(offer, matching)
        else:
            d = 0

        if d > 0:
            applied.append({
                'name':     offer.name,
                'label':    offer.description or offer.name,
                'discount': d,
            })
            total += d

    return total, applied


# ──────────────────────────────────────────────────────────────────────────────

def calculate_delivery_and_final(subtotal):
    try:
        from .models import SiteSettings
        s = SiteSettings.get()
        free_limit = s.free_delivery_min_order
        charge = s.delivery_charge
    except Exception:
        free_limit = FREE_DELIVERY_LIMIT
        charge = DELIVERY_CHARGE
    if subtotal >= free_limit:
        return 0, subtotal, 0
    remaining = free_limit - subtotal
    return charge, subtotal + charge, remaining


def send_new_product_alert(product):
    """Email all active newsletter subscribers about a newly added product."""
    import threading
    from django.template.loader import render_to_string
    from .models import NewsletterSubscriber

    subscribers = list(
        NewsletterSubscriber.objects.filter(is_active=True).values_list("email", "token")
    )
    if not subscribers:
        return

    subject = f"New Arrival: {product.name} | Zivo"
    product_url = f"{settings.SITE_URL}/product/{product.id}/"

    def _send():
        try:
            from brevo import Brevo, SendTransacEmailRequestToItem, SendTransacEmailRequestSender
            client = Brevo(api_key=settings.BREVO_API_KEY)
            sender = SendTransacEmailRequestSender(email=settings.DEFAULT_FROM_EMAIL, name="Zivo")
            for email, token in subscribers:
                html = render_to_string("store/emails/new_product_alert.html", {
                    "product": product,
                    "store_name": "Zivo",
                    "site_url": settings.SITE_URL,
                    "product_url": product_url,
                    "unsubscribe_token": token,
                })
                try:
                    client.transactional_emails.send_transac_email(
                        to=[SendTransacEmailRequestToItem(email=email)],
                        sender=sender,
                        subject=subject,
                        html_content=html,
                    )
                except Exception as e:
                    print(f"[EMAIL ERROR] Failed to send to {email}: {e}")
        except Exception as e:
            print(f"[EMAIL ERROR] New product alert failed for product {product.id}: {e}")

    threading.Thread(target=_send, daemon=False).start()


def send_whatsapp(phone, message):
    if not phone:
        return

    # Normalize Indian mobile numbers (stored as 10-digit strings in DB)
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("0"):
        phone = phone[1:]
    if not phone.startswith("+"):
        if len(phone) == 10 and phone.isdigit():
            phone = "+91" + phone
        else:
            print("Cannot normalize phone, skipping WhatsApp:", phone)
            return

    try:
        from twilio.rest import Client
        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )
        client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=f"whatsapp:{phone}",
            body=message
        )
        print("WhatsApp sent to:", phone)
    except Exception as e:
        print("WhatsApp send failed:", e)
