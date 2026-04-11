import logging
from django.conf import settings

logger = logging.getLogger("store")

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
            logger.error(f"Email send failed — {template_name} for order {order_id}: {e}", exc_info=True)

    # daemon=False: order emails are critical — let this thread finish even during shutdown
    threading.Thread(target=_send, daemon=False).start()


# ─────────────────────────── Exchange emails ─────────────────────────────────

def send_exchange_email(exchange, template_name, subject):
    """Send a size-exchange email to the customer in a background thread."""
    import threading
    from django.template.loader import render_to_string

    customer = exchange.order.customer
    if not customer or not customer.email:
        return

    html = render_to_string(f"store/emails/{template_name}", {
        "exchange": exchange,
        "store_name": "Zivo",
        "site_url": settings.SITE_URL,
    })
    recipient = customer.email

    def _send():
        try:
            from brevo import Brevo, SendTransacEmailRequestToItem, SendTransacEmailRequestSender
            client = Brevo(api_key=settings.BREVO_API_KEY)
            client.transactional_emails.send_transac_email(
                to=[SendTransacEmailRequestToItem(email=recipient)],
                sender=SendTransacEmailRequestSender(
                    email=settings.DEFAULT_FROM_EMAIL, name="Zivo"
                ),
                subject=subject,
                html_content=html,
            )
        except Exception as e:
            logger.error(f"[EXCHANGE EMAIL] {template_name} for exchange #{exchange.id}: {e}", exc_info=True)

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
                    logger.error(f"Product alert email failed for {email} (product {product_id}): {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Product alert batch failed for product {product_id}: {e}", exc_info=True)

    # daemon=True: bulk newsletter — don't block server shutdown waiting for all sends
    transaction.on_commit(lambda: threading.Thread(target=_send, daemon=True).start())


# ─────────────────────────── In-app notifications ────────────────────────────

def create_notification(customer, title, message, notif_type="ORDER", link=""):
    """Create a UserNotification for the bell dropdown."""
    try:
        from .models import UserNotification
        UserNotification.objects.create(
            customer=customer, title=title, message=message,
            notif_type=notif_type, link=link,
        )
    except Exception as e:
        logger.error(f"create_notification failed: {e}", exc_info=True)


# ─────────────────────────── Browser push ────────────────────────────────────

def _send_one_push(endpoint, p256dh, auth, title, body, url="/"):
    try:
        from pywebpush import webpush
        import json
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{settings.DEFAULT_FROM_EMAIL}"},
        )
    except Exception as e:
        logger.error(f"Push send failed [{endpoint[:40]}]: {e}")


def send_push_to_user(customer, title, body, url="/"):
    """Send browser push to all subscriptions of one customer (background)."""
    import threading
    from .models import PushSubscription
    subs = list(PushSubscription.objects.filter(customer=customer).values("endpoint", "p256dh", "auth"))
    if not subs:
        return
    def _send():
        for s in subs:
            _send_one_push(s["endpoint"], s["p256dh"], s["auth"], title, body, url)
    threading.Thread(target=_send, daemon=True).start()


def send_push_to_all(title, body, url="/"):
    """Broadcast browser push to every subscribed user (background)."""
    import threading
    from .models import PushSubscription
    subs = list(PushSubscription.objects.all().values("endpoint", "p256dh", "auth"))
    if not subs:
        return
    def _send():
        for s in subs:
            _send_one_push(s["endpoint"], s["p256dh"], s["auth"], title, body, url)
    threading.Thread(target=_send, daemon=True).start()


def send_otp_sms(phone, otp):
    """Send OTP via 2Factor.in. phone should be 10-digit Indian mobile number."""
    import requests
    # Strip country code if present
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+91"):
        phone = phone[3:]
    elif phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]
    try:
        response = requests.get(
            f"https://2factor.in/API/V1/{settings.TWO_FACTOR_API_KEY}/SMS/{phone}/{otp}/THEZIVO",
            timeout=10,
        )
        result = response.json()
        if result.get("Status") != "Success":
            logger.error(f"2Factor SMS rejected for {phone}: {result}")
            return False
        return True
    except Exception as e:
        logger.error(f"2Factor SMS failed for {phone}: {e}", exc_info=True)
        return False


def _whatsapp_async(phone, message):
    """Run send_whatsapp in a background thread so it never blocks the caller."""
    import threading
    # daemon=True: WhatsApp alerts are non-critical — don't block server shutdown
    threading.Thread(target=send_whatsapp, args=(phone, message), daemon=True).start()


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


def email_new_order_admin(order):
    """Send a new order alert email to the store owner."""
    import threading
    admin_email = getattr(settings, 'ADMIN_ALERT_EMAIL', '')
    if not admin_email:
        return

    def _send():
        try:
            items = list(order.items.select_related('sku__product').all())
            items_text = "\n".join(
                f"  • {item.sku.product.name} ({item.sku.size} / {item.sku.color}) × {item.quantity} — ₹{item.price}"
                for item in items
            )
            html = f"""
            <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;background:#f9f9f9;">
              <div style="background:#111;padding:20px 24px;border-radius:10px 10px 0 0;">
                <h2 style="color:#fff;margin:0;font-size:1.1rem;">🛒 New Order #{order.id}</h2>
              </div>
              <div style="background:#fff;padding:24px;border-radius:0 0 10px 10px;border:1px solid #e5e7eb;">
                <p style="margin:0 0 16px;font-size:0.9rem;color:#374151;">
                  <strong>Customer:</strong> {order.name} &nbsp;·&nbsp; {order.phone}<br>
                  <strong>Payment:</strong> {order.get_payment_method_display()} &nbsp;·&nbsp; ₹{order.total_amount}<br>
                  <strong>City:</strong> {order.city or '—'}
                </p>
                <div style="background:#f3f4f6;border-radius:8px;padding:14px 16px;font-size:0.85rem;color:#374151;white-space:pre-line;">{items_text}</div>
                <div style="margin-top:20px;">
                  <a href="{settings.SITE_URL}/admin/store/order/{order.id}/change/"
                     style="background:#8b5cf6;color:#fff;padding:10px 20px;border-radius:7px;
                            text-decoration:none;font-size:0.88rem;font-weight:600;">
                    View Order in Admin →
                  </a>
                </div>
              </div>
            </div>
            """
            from brevo import Brevo, SendTransacEmailRequestToItem, SendTransacEmailRequestSender
            client = Brevo(api_key=settings.BREVO_API_KEY)
            client.transactional_emails.send_transac_email(
                to=[SendTransacEmailRequestToItem(email=admin_email)],
                sender=SendTransacEmailRequestSender(email=settings.DEFAULT_FROM_EMAIL, name='Zivo'),
                subject=f"New Order #{order.id} — ₹{order.total_amount} ({order.get_payment_method_display()})",
                html_content=html,
            )
        except Exception as e:
            logger.error(f"Admin order alert email failed for order {order.id}: {e}", exc_info=True)

    threading.Thread(target=_send, daemon=True).start()


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
            logger.warning(f"Cannot normalize phone for WhatsApp, skipping: {phone}")
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
        logger.info(f"WhatsApp sent to {phone}")
    except Exception as e:
        logger.error(f"WhatsApp send failed for {phone}: {e}", exc_info=True)


# ── Shiprocket ────────────────────────────────────────────────────────────────

def _get_shiprocket_token():
    """Fetch (or return cached) Shiprocket JWT. Token is valid 10 days; cached for 9."""
    from django.core.cache import cache
    token = cache.get("shiprocket_token")
    if token:
        return token
    import requests
    resp = requests.post(
        "https://apiv2.shiprocket.in/v1/external/auth/login",
        json={"email": settings.SHIPROCKET_EMAIL, "password": settings.SHIPROCKET_PASSWORD},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["token"]
    cache.set("shiprocket_token", token, timeout=9 * 24 * 3600)
    return token


def create_shiprocket_shipment(order):
    """Create a Shiprocket shipment and save AWB/tracking URL back to the order.
    Runs in a background daemon thread — safe to call from admin views."""
    import threading

    def _run():
        try:
            import requests as req
            token = _get_shiprocket_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            items = []
            total_weight_kg = 0.0
            for item in order.items.select_related("sku__product").all():
                weight_kg = (item.sku.weight_grams * item.quantity) / 1000
                total_weight_kg += weight_kg
                items.append({
                    "name": item.sku.product.name,
                    "sku": item.sku.sku_code,
                    "units": item.quantity,
                    "selling_price": str(item.price),
                    "discount": "0",
                    "tax": "0",
                    "hsn": "",
                })

            if total_weight_kg < 0.1:
                total_weight_kg = 0.2  # Shiprocket minimum

            name_parts = order.name.strip().split(" ", 1)
            first_name = name_parts[0]
            last_name  = name_parts[1] if len(name_parts) > 1 else "."

            payload = {
                "order_id": order.invoice_number,
                "order_date": order.created_at.strftime("%Y-%m-%d %H:%M"),
                "pickup_location": settings.SHIPROCKET_PICKUP_LOCATION,
                "billing_customer_name": first_name,
                "billing_last_name": last_name,
                "billing_address": order.address,
                "billing_city": order.city or "",
                "billing_pincode": order.pincode or "",
                "billing_state": order.state or "",
                "billing_country": "India",
                "billing_email": order.contact_email or "",
                "billing_phone": order.phone,
                "shipping_is_billing": True,
                "order_items": items,
                "payment_method": "COD" if order.payment_method == "COD" else "Prepaid",
                "sub_total": str(int(order.items_subtotal)),
                "length": 25,
                "breadth": 20,
                "height": 5,
                "weight": round(total_weight_kg, 2),
            }
            if settings.SHIPROCKET_CHANNEL_ID:
                payload["channel_id"] = settings.SHIPROCKET_CHANNEL_ID

            resp = req.post(
                "https://apiv2.shiprocket.in/v1/external/orders/create/adhoc",
                json=payload,
                headers=headers,
                timeout=20,
            )
            data = resp.json()
            shipment_id = data.get("shipment_id")

            if not shipment_id:
                logger.warning(f"[SHIPROCKET] Order {order.id} — no shipment_id: {data}")
                return

            # Step 2: assign courier + get AWB
            import time
            time.sleep(3)  # brief wait for Shiprocket to register the shipment
            awb_resp = req.post(
                "https://apiv2.shiprocket.in/v1/external/courier/assign/awb",
                json={"shipment_id": [shipment_id]},
                headers=headers,
                timeout=20,
            )
            awb_data = awb_resp.json()
            response_data = (awb_data.get("response") or {}).get("data") or {}
            awb      = response_data.get("awb_code", "")
            courier  = response_data.get("courier_name", "")
            tracking = f"https://shiprocket.co/tracking/{awb}" if awb else ""

            if awb:
                from .models import Order
                Order.objects.filter(pk=order.pk).update(
                    awb_number=awb,
                    courier_name=courier,
                    tracking_url=tracking,
                )
                logger.info(f"[SHIPROCKET] Order {order.id} — AWB {awb} ({courier})")
            else:
                logger.warning(f"[SHIPROCKET] Order {order.id} — no AWB in response: {awb_data}")

        except Exception as e:
            logger.error(f"[SHIPROCKET] Order {order.id} failed: {e}", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
