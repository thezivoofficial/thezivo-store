from django.conf import settings

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


def calculate_delivery_and_final(subtotal):
    if subtotal >= FREE_DELIVERY_LIMIT:
        return 0, subtotal, 0
    remaining = FREE_DELIVERY_LIMIT - subtotal
    return DELIVERY_CHARGE, subtotal + DELIVERY_CHARGE, remaining


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
