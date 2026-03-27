from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField, Q
from django.utils import timezone
from datetime import timedelta
from .models import Order, SKU, OrderItem


# Revenue filters
# Order-level: online PAID  OR  COD that reached DELIVERED (cash collected)
ORDER_CONFIRMED = Q(payment_status="PAID") | Q(payment_method="COD", status="DELIVERED")

# OrderItem-level: same logic via order FK
ITEM_CONFIRMED = (
    Q(order__payment_status="PAID") |
    Q(order__payment_method="COD", order__status="DELIVERED")
)

# Active orders only (exclude bare CREATED and CANCELLED)
ITEM_ACTIVE = Q(order__status__in=["PLACED", "CONFIRMED", "SHIPPED", "DELIVERED"])


def dashboard_callback(request, context):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start  = today_start - timedelta(days=7)
    month_start = today_start.replace(day=1)

    # ── Revenue cards ────────────────────────────────────────────────────────
    def confirmed_revenue(extra_filter=None):
        qs = Order.objects.filter(ORDER_CONFIRMED)
        if extra_filter:
            qs = qs.filter(**extra_filter)
        return qs.aggregate(total=Sum("total_amount"))["total"] or 0

    context["revenue_today"]  = confirmed_revenue({"created_at__gte": today_start})
    context["revenue_week"]   = confirmed_revenue({"created_at__gte": week_start})
    context["revenue_month"]  = confirmed_revenue({"created_at__gte": month_start})
    context["revenue_total"]  = confirmed_revenue()

    # ── Order counts ─────────────────────────────────────────────────────────
    context["orders_today"]   = Order.objects.filter(
        created_at__gte=today_start
    ).exclude(status__in=["CREATED", "CANCELLED"]).count()
    context["orders_placed"]  = Order.objects.filter(status="PLACED").count()
    context["orders_shipped"] = Order.objects.filter(status="SHIPPED").count()

    # ── Orders-by-status (donut chart) ───────────────────────────────────────
    STATUS_COLORS = {
        "PLACED":    "#3b82f6",
        "CONFIRMED": "#f59e0b",
        "SHIPPED":   "#8b5cf6",
        "DELIVERED": "#10b981",
        "CANCELLED": "#ef4444",
        "CREATED":   "#9ca3af",
    }
    status_qs = (
        Order.objects
        .values("status")
        .annotate(count=Count("id"))
        .order_by("status")
    )
    context["status_labels"] = [r["status"] for r in status_qs]
    context["status_data"]   = [r["count"] for r in status_qs]
    context["status_colors"] = [STATUS_COLORS.get(r["status"], "#6b7280") for r in status_qs]

    # ── Revenue last 30 days (line chart) ────────────────────────────────────
    days_labels, day_revenues = [], []
    for i in range(29, -1, -1):
        day      = today_start - timedelta(days=i)
        next_day = day + timedelta(days=1)
        rev = (
            Order.objects
            .filter(ORDER_CONFIRMED, created_at__gte=day, created_at__lt=next_day)
            .aggregate(total=Sum("total_amount"))["total"] or 0
        )
        days_labels.append(day.strftime("%d %b"))
        day_revenues.append(float(rev))

    context["chart_days"]     = days_labels
    context["chart_revenues"] = day_revenues

    # ── Top 5 selling products (bar chart) ───────────────────────────────────
    top = (
        OrderItem.objects
        .filter(ITEM_ACTIVE)
        .values("sku__product__name")
        .annotate(total_sold=Sum("quantity"))
        .order_by("-total_sold")[:5]
    )
    context["top_product_names"] = [p["sku__product__name"] for p in top]
    context["top_product_sales"] = [p["total_sold"] for p in top]

    # ── Low stock alerts ─────────────────────────────────────────────────────
    context["low_stock"] = (
        SKU.objects
        .filter(stock__lte=5)
        .select_related("product")
        .order_by("stock")[:10]
    )

    # ── Revenue by category ──────────────────────────────────────────────────
    cat_qs = (
        OrderItem.objects
        .filter(ITEM_CONFIRMED)
        .values("sku__product__category__name")
        .annotate(rev=Sum(
            ExpressionWrapper(F("quantity") * F("price"), output_field=DecimalField())
        ))
        .order_by("-rev")
    )
    context["cat_labels"] = [r["sku__product__category__name"] or "Uncategorised" for r in cat_qs]
    context["cat_data"]   = [float(r["rev"]) for r in cat_qs]

    # ── Revenue by gender ────────────────────────────────────────────────────
    gen_qs = (
        OrderItem.objects
        .filter(ITEM_CONFIRMED)
        .values("sku__product__gender")
        .annotate(rev=Sum(
            ExpressionWrapper(F("quantity") * F("price"), output_field=DecimalField())
        ))
        .order_by("-rev")
    )
    context["gen_labels"] = [r["sku__product__gender"].capitalize() for r in gen_qs]
    context["gen_data"]   = [float(r["rev"]) for r in gen_qs]

    # ── Top customers ────────────────────────────────────────────────────────
    context["top_customers"] = (
        Order.objects
        .filter(ORDER_CONFIRMED)
        .values("customer__id", "customer__name", "customer__phone", "name", "phone")
        .annotate(
            order_count=Count("id"),
            total_spent=Sum("total_amount"),
        )
        .order_by("-total_spent")[:8]
    )

    # ── Recent orders ────────────────────────────────────────────────────────
    context["recent_orders"] = (
        Order.objects
        .order_by("-created_at")[:8]
    )

    return context


def pending_orders_badge(request):
    count = Order.objects.filter(status="PLACED").count()
    return str(count) if count else None
