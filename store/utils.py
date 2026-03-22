from django.conf import settings

# Fallback defaults used if DB is unavailable (e.g. during migrations)
FREE_DELIVERY_LIMIT = 799
DELIVERY_CHARGE = 59


def send_order_email(order, template_name, subject):
    """Send an HTML order email via Brevo HTTP API in a background thread."""
    import threading
    from django.template.loader import render_to_string

    recipient = order.contact_email
    if not recipient:
        return

    items = order.items.select_related('sku__product').all()
    html = render_to_string(f'store/emails/{template_name}', {
        'order': order,
        'items': items,
        'store_name': 'Zivo',
        'site_url': settings.SITE_URL,
    })
    order_id = order.id

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
            if 'confirmation' in template_name:
                flag = 'confirmation_email_sent'
            elif 'shipped' in template_name:
                flag = 'shipped_email_sent'
            else:
                flag = 'delivered_email_sent'
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


def send_new_product_alert(product_id):
    """Email all active newsletter subscribers about a newly added product.
    Call with just the product ID — all work happens in a background thread
    after the DB transaction commits, so the save button returns instantly."""
    import threading
    from django.db import transaction

    def _send():
        try:
            from django.template.loader import render_to_string
            from .models import NewsletterSubscriber, Product
            from brevo import Brevo, SendTransacEmailRequestToItem, SendTransacEmailRequestSender

            prod = Product.objects.select_related('category').prefetch_related('sku_set').get(id=product_id)
            sku  = prod.sku_set.first()
            product_ctx = {
                "name":          prod.name,
                "image_url":     prod.image.url if prod.image else None,
                "category":      str(prod.category),
                "gender":        prod.get_gender_display(),
                "selling_price": sku.selling_price if sku else None,
                "mrp":           sku.mrp if sku else None,
            }
            product_url = f"{settings.SITE_URL}/product/{product_id}/"
            subject     = f"New Arrival: {prod.name} | Zivo"

            subscribers = list(
                NewsletterSubscriber.objects.filter(is_active=True).values_list("email", "token")
            )
            if not subscribers:
                return

            client = Brevo(api_key=settings.BREVO_API_KEY)
            sender = SendTransacEmailRequestSender(email=settings.DEFAULT_FROM_EMAIL, name="Zivo")
            for email, token in subscribers:
                html = render_to_string("store/emails/new_product_alert.html", {
                    "product":           product_ctx,
                    "store_name":        "Zivo",
                    "site_url":          settings.SITE_URL,
                    "product_url":       product_url,
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
            print(f"[EMAIL ERROR] New product alert failed for product {product_id}: {e}")

    transaction.on_commit(lambda: threading.Thread(target=_send, daemon=False).start())


def send_otp_sms(phone, otp):
    """Send OTP via Fast2SMS. phone should be 10-digit Indian mobile number."""
    import requests
    # Strip country code if present
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+91"):
        phone = phone[3:]
    elif phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]
    try:
        response = requests.post(
            "https://www.fast2sms.com/dev/bulkV2",
            headers={"authorization": settings.FAST2SMS_API_KEY},
            data={
                "route": "otp",
                "variables_values": otp,
                "flash": "0",
                "numbers": phone,
            },
            timeout=10,
        )
        result = response.json()
        if not result.get("return"):
            print(f"[SMS ERROR] Fast2SMS rejected: {result}")
        return result.get("return", False)
    except Exception as e:
        print(f"[SMS ERROR] Fast2SMS failed: {e}")
        return False


def _whatsapp_async(phone, message):
    """Run send_whatsapp in a background thread so it never blocks the caller."""
    import threading
    threading.Thread(target=send_whatsapp, args=(phone, message), daemon=False).start()


def whatsapp_new_order_admin(order):
    """Alert the store admin on every new confirmed/placed order."""
    admin_phone = getattr(settings, 'ADMIN_ALERT_PHONE', '')
    if not admin_phone:
        return
    try:
        items_count = order.items.count()
    except Exception:
        items_count = '?'
    msg = (
        f"🛒 *New Order #{order.id}*\n"
        f"👤 {order.name} · {order.phone}\n"
        f"💰 ₹{order.total_amount} · {order.get_payment_method_display()}\n"
        f"📦 {items_count} item(s)\n"
        f"🔗 {settings.SITE_URL}/admin/store/order/{order.id}/change/"
    )
    _whatsapp_async(admin_phone, msg)


def whatsapp_order_shipped(order):
    """Notify the customer that their order has been shipped."""
    if not order.phone:
        return
    msg = (
        f"📦 *Your order has been shipped!*\n\n"
        f"Hi {order.name}, your *Zivo* order *#{order.id}* is on its way! 🚚\n\n"
        f"Expected delivery in 3–5 business days.\n\n"
        f"View your order: {settings.SITE_URL}/orders/{order.id}/\n"
        f"Questions? Email: support@thezivo.com"
    )
    _whatsapp_async(order.phone, msg)


def whatsapp_order_delivered(order):
    """Notify the customer that their order has been delivered."""
    if not order.phone:
        return
    msg = (
        f"✅ *Order Delivered!*\n\n"
        f"Hi {order.name}, your *Zivo* order *#{order.id}* has been delivered. 🎉\n\n"
        f"We hope you love your purchase!\n"
        f"Rate your order: {settings.SITE_URL}/orders/{order.id}/\n\n"
        f"Need help? Email: support@thezivo.com"
    )
    _whatsapp_async(order.phone, msg)


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
