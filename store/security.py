"""
Admin security helpers:
- WhatsApp alert on every successful admin login
- Custom axes lockout response (friendly error page)
"""
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.http import HttpResponse
from django.conf import settings


@receiver(user_logged_in)
def admin_login_alert(sender, request, user, **kwargs):
    """Send a WhatsApp alert whenever a staff/superuser logs into admin."""
    if not user.is_staff:
        return
    phone = getattr(settings, "ADMIN_ALERT_PHONE", "")
    if not phone:
        return

    import datetime
    import threading
    from .utils import send_whatsapp

    now = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR", "unknown")
    )
    msg = (
        f"🔐 *Zivo Admin Login*\n\n"
        f"👤 User: {user.username}\n"
        f"🌐 IP: {ip}\n"
        f"🕐 Time: {now}"
    )

    # Run in background so the login response is never delayed by Twilio
    threading.Thread(target=send_whatsapp, args=(phone, msg), daemon=True).start()


def axes_lockout_response(request, credentials, *args, **kwargs):
    """Return a friendly 403 page when axes locks out an IP."""
    return HttpResponse(
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Access Locked — Zivo Admin</title>
<style>
  body { font-family: sans-serif; display:flex; align-items:center;
         justify-content:center; min-height:100vh; margin:0;
         background:#f9fafb; }
  .box { text-align:center; padding:40px; background:#fff;
         border-radius:12px; box-shadow:0 2px 12px rgba(0,0,0,.1);
         max-width:400px; }
  h1   { color:#ef4444; font-size:22px; margin-bottom:8px; }
  p    { color:#6b7280; font-size:14px; line-height:1.6; }
</style>
</head>
<body>
  <div class="box">
    <h1>🔒 Access Temporarily Locked</h1>
    <p>Too many failed login attempts.<br>
       Please wait <strong>15 minutes</strong> before trying again.</p>
  </div>
</body>
</html>""",
        status=403,
        content_type="text/html",
    )
