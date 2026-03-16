from django.conf import settings

FREE_DELIVERY_LIMIT = 799
DELIVERY_CHARGE = 59


def send_order_email(order, template_name, subject):
    """Send an HTML order email. Errors are logged but never crash the request."""
    from django.template.loader import render_to_string
    from django.core.mail import send_mail

    customer = order.customer
    if not customer or not customer.email:
        return

    items = order.items.select_related('sku__product').all()
    html = render_to_string(f'store/emails/{template_name}', {
        'order': order,
        'items': items,
        'store_name': 'Zivo',
    })

    try:
        send_mail(
            subject,
            '',
            settings.DEFAULT_FROM_EMAIL,
            [customer.email],
            html_message=html,
            fail_silently=False,
        )
        flag = 'confirmation_email_sent' if 'confirmation' in template_name else 'shipped_email_sent'
        type(order).objects.filter(id=order.id).update(**{flag: True})
    except BaseException as e:
        print(f"[EMAIL ERROR] Failed to send {template_name} for order {order.id}: {e}")


def calculate_delivery_and_final(subtotal):
    if subtotal >= FREE_DELIVERY_LIMIT:
        return 0, subtotal, 0
    remaining = FREE_DELIVERY_LIMIT - subtotal
    return DELIVERY_CHARGE, subtotal + DELIVERY_CHARGE, remaining


def send_whatsapp(phone, message):
    if not phone or not phone.startswith("+"):
        print("Invalid phone format, skipping WhatsApp:", phone)
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