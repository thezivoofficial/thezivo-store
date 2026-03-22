"""
Management command: send cart abandonment reminder emails.

Run via cron / Railway cron job every hour:
    python manage.py send_abandonment_emails

Logic:
- Find AbandonedCart records where:
    • updated_at is between 24h and 48h ago  (sweet spot — not too soon, not too stale)
    • email_sent is False
    • the customer has a valid email
    • the customer has NOT placed any order after the cart was last updated
- Send reminder email, mark email_sent=True
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = "Send cart abandonment reminder emails to customers who left items in their cart."

    def handle(self, *args, **options):
        from store.models import AbandonedCart, Order
        from store.utils import send_order_email  # reuse Brevo mailer

        now = timezone.now()
        window_start = now - timedelta(hours=48)
        window_end   = now - timedelta(hours=24)

        carts = AbandonedCart.objects.filter(
            updated_at__gte=window_start,
            updated_at__lte=window_end,
            email_sent=False,
            customer__email__isnull=False,
        ).exclude(customer__email="").select_related("customer")

        sent = 0
        for cart in carts:
            customer = cart.customer
            # Skip if customer placed an order after abandoning the cart
            has_recent_order = Order.objects.filter(
                customer=customer,
                created_at__gt=cart.updated_at,
            ).exists()
            if has_recent_order:
                cart.email_sent = True
                cart.save(update_fields=["email_sent"])
                continue

            if not cart.items_snapshot:
                continue

            try:
                _send_abandonment_email(customer, cart)
                cart.email_sent = True
                cart.save(update_fields=["email_sent"])
                sent += 1
                self.stdout.write(f"  Sent to {customer.email}")
            except Exception as e:
                self.stderr.write(f"  Failed for {customer.email}: {e}")

        self.stdout.write(self.style.SUCCESS(f"Done — {sent} abandonment email(s) sent."))


def _send_abandonment_email(customer, cart):
    import threading
    from django.template.loader import render_to_string
    from django.conf import settings

    html = render_to_string("store/emails/cart_abandonment.html", {
        "customer":   customer,
        "items":      cart.items_snapshot,
        "store_name": "Zivo",
        "site_url":   settings.SITE_URL,
        "cart_url":   f"{settings.SITE_URL}/cart/",
    })
    subject = "You left something behind 🛍️ | Zivo"
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
            print(f"[EMAIL ERROR] Abandonment email failed for {recipient}: {e}")

    threading.Thread(target=_send, daemon=False).start()
