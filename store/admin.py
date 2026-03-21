import csv
import io
import base64
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User
from .forms import StaffUserCreationForm
from django.db.models import Sum, Count, F, DecimalField, ExpressionWrapper
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from .models import Product, SKU, Order, OrderItem, StockNotification, ProductImage, Address, Customer, SiteSettings, Coupon, Review, Announcement, Category, Offer, NewsletterSubscriber, ReturnRequest, ReturnItem
from .utils import send_whatsapp, send_order_email, send_new_product_alert
from django.conf import settings


def _make_qr_b64(url: str) -> str:
    """Return a base64-encoded PNG of a QR code for the given URL."""
    import qrcode
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── Inlines ──────────────────────────────────────────────────────────────────

class ProductImageInline(TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image", "color", "is_primary", "preview")
    readonly_fields = ("preview",)

    def get_formset(self, request, obj=None, **kwargs):
        """Populate the color field as a dropdown of this product's SKU colors."""
        formset = super().get_formset(request, obj, **kwargs)
        if obj:
            from django import forms as django_forms
            colors = list(
                SKU.objects.filter(product=obj)
                .values_list('color', flat=True)
                .distinct().order_by('color')
            )
            choices = [('', '— Shared (shown for all colors) —')] + [(c, c) for c in colors]
            formset.form.base_fields['color'].widget = django_forms.Select(
                choices=choices, attrs={'style': 'min-width:200px'}
            )
            formset.form.base_fields['color'].required = False
            formset.form.base_fields['color'].help_text = (
                "Pick a color to link this image to that variant, "
                "or leave as Shared to show it for all colors."
            )
        return formset

    def preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="height:80px;border-radius:6px;object-fit:cover;">',
                obj.image.url,
            )
        return "—"
    preview.short_description = "Preview"


class SKUInline(TabularInline):
    model = SKU
    extra = 1
    fields = ("sku_code", "size", "color", "mrp", "selling_price", "stock", "stock_badge")
    readonly_fields = ("stock_badge",)

    def stock_badge(self, obj):
        if not obj.pk:
            return "—"
        if obj.stock == 0:
            return format_html('<span class="zivo-badge zivo-red">Out of Stock</span>')
        if obj.stock <= 5:
            return format_html('<span class="zivo-badge zivo-yellow">⚠ {} left</span>', obj.stock)
        return format_html('<span class="zivo-badge zivo-green">✓ {} in stock</span>', obj.stock)
    stock_badge.short_description = "Status"


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("sku", "quantity", "price", "item_total")
    fields = ("sku", "quantity", "price", "item_total")
    can_delete = False

    def item_total(self, obj):
        return format_html("<strong>₹{}</strong>", int(obj.price * obj.quantity))
    item_total.short_description = "Total"

    def has_add_permission(self, request, obj=None):
        return False


# ── Address ───────────────────────────────────────────────────────────────────

@admin.register(Address)
class AddressAdmin(ModelAdmin):
    list_display = ("customer", "name", "city", "state", "pincode", "is_default")
    list_filter = ("state", "is_default")
    search_fields = ("customer__name", "customer__phone", "name", "city", "pincode")


# ── Product ───────────────────────────────────────────────────────────────────

@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display  = ("cat_thumbnail", "name", "slug", "gender", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    list_filter   = ("gender", "is_active")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    def cat_thumbnail(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="height:40px;width:56px;object-fit:cover;border-radius:6px;">',
                obj.image.url,
            )
        return format_html('<span style="color:#9ca3af;font-size:11px;">No image</span>')
    cat_thumbnail.short_description = ""


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display        = ("thumbnail", "name", "brand", "gender", "category", "sku_count", "stock_status", "trending_badge", "active", "is_trending")
    list_filter         = ("active", "is_trending", "gender", "category", "brand")
    search_fields       = ("name", "brand")
    list_editable       = ("active", "is_trending")
    list_per_page       = 25
    list_select_related = True
    inlines             = [ProductImageInline, SKUInline]
    compressed_fields   = True
    actions             = ["mark_trending", "unmark_trending"]

    fieldsets = (
        ("Basic Info", {
            "fields": ("name", "brand"),
        }),
        ("Categorisation", {
            "fields": ("gender", "category"),
        }),
        ("Description", {
            "fields": ("description",),
        }),
        ("Material & Care", {
            "fields": ("material", "care"),
        }),
        ("Fallback Image", {
            "description": "Used as thumbnail in the admin list and email templates. "
                           "For storefront display, upload color-tagged images in the Product Images section below.",
            "fields": ("image",),
        }),
        ("Visibility", {
            "fields": ("active", "is_trending"),
        }),
    )

    @admin.action(description="⭐ Mark selected as Trending")
    def mark_trending(self, request, queryset):
        updated = queryset.update(is_trending=True)
        self.message_user(request, f"{updated} product(s) marked as trending.")

    @admin.action(description="✕ Remove selected from Trending")
    def unmark_trending(self, request, queryset):
        updated = queryset.update(is_trending=False)
        self.message_user(request, f"{updated} product(s) removed from trending.")

    def trending_badge(self, obj):
        if obj.is_trending:
            return format_html('<span class="zivo-badge zivo-purple">⭐ Trending</span>')
        return format_html('<span style="color:#d1d5db;font-size:11px;">—</span>')
    trending_badge.short_description = "Trending"

    class Media:
        css = {"all": ("store/admin.css",)}
        js = ("store/admin_sku.js",)

    def thumbnail(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="height:48px;width:48px;object-fit:cover;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.15);">',
                obj.image.url,
            )
        return format_html('<span style="color:#9ca3af;font-size:11px;">—</span>')
    thumbnail.short_description = ""

    def sku_count(self, obj):
        count = obj.sku_set.count()
        if count == 0:
            return format_html('<span class="zivo-badge zivo-red">⚠ No SKUs</span>')
        return format_html(
            '<span class="zivo-badge zivo-green">✓ {} SKU{}</span>',
            count, "s" if count != 1 else "",
        )
    sku_count.short_description = "SKUs"

    def stock_status(self, obj):
        total = obj.sku_set.aggregate(t=Sum("stock"))["t"] or 0
        if total == 0:
            return format_html('<span class="zivo-badge zivo-red">Out of Stock</span>')
        if total <= 10:
            return format_html('<span class="zivo-badge zivo-yellow">Low: {}</span>', total)
        return format_html('<span class="zivo-badge zivo-green">{} in stock</span>', total)
    stock_status.short_description = "Stock"

    def save_model(self, request, obj, form, change):
        is_new = obj.pk is None
        super().save_model(request, obj, form, change)
        if is_new and obj.active:
            send_new_product_alert(obj.pk)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        response = super().change_view(request, object_id, form_url, extra_context)
        product = self.get_object(request, object_id)
        if product and not product.sku_set.exists():
            messages.warning(
                request,
                "⚠ This product has no SKUs. It will not appear in the store until at least one SKU is added.",
            )
        return response


# ── Newsletter Subscribers ─────────────────────────────────────────────────────

@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(ModelAdmin):
    list_display  = ("email", "subscribed_at", "is_active")
    list_editable = ("is_active",)
    list_filter   = ("is_active",)
    search_fields = ("email",)


# ── SKU ───────────────────────────────────────────────────────────────────────

@admin.register(SKU)
class SKUAdmin(ModelAdmin):
    list_display = (
        "sku_code", "product", "size", "color",
        "mrp", "selling_price", "stock_badge", "sold_quantity",
    )
    list_display_links  = ("sku_code", "product")
    search_fields       = ("sku_code", "product__name", "color")
    list_filter         = ("size", "product__gender", "product__category")
    list_editable       = ("mrp", "selling_price")
    list_per_page       = 30
    compressed_fields   = True
    change_list_template = "admin/sku_analytics.html"

    actions = ["print_sku_labels"]

    def get_urls(self):
        custom = [
            path(
                "bulk-stock/",
                self.admin_site.admin_view(self.bulk_stock_view),
                name="store_sku_bulk_stock",
            ),
            path(
                "print-labels/",
                self.admin_site.admin_view(self.print_labels_view),
                name="store_sku_print_labels",
            ),
        ]
        return custom + super().get_urls()

    def print_sku_labels(self, request, queryset):
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return redirect(f"{reverse('admin:store_sku_print_labels')}?ids={ids}")
    print_sku_labels.short_description = "🏷️ Print SKU labels"

    def print_labels_view(self, request):
        ids_param = request.GET.get("ids", "")
        if ids_param:
            ids = [int(i) for i in ids_param.split(",") if i.isdigit()]
            skus = SKU.objects.filter(pk__in=ids).select_related("product").order_by("product__name", "size", "color")
        else:
            skus = SKU.objects.select_related("product").order_by("product__name", "size", "color")
        return render(request, "admin/sku_labels.html", {
            "skus": skus,
            "title": "SKU Labels",
            "opts": self.model._meta,
        })

    def bulk_stock_view(self, request):
        skus = SKU.objects.select_related("product").order_by("product__name", "size", "color")

        if request.method == "POST":
            updated = 0
            for sku in skus:
                key = f"stock_{sku.pk}"
                if key in request.POST:
                    try:
                        new_stock = int(request.POST[key])
                        if new_stock != sku.stock:
                            sku.stock = new_stock
                            sku.save()
                            updated += 1
                    except ValueError:
                        pass
            messages.success(request, f"✓ {updated} SKU stock(s) updated.")
            return redirect("..")

        # Group by product for easier display
        products = {}
        for sku in skus:
            pname = sku.product.name
            if pname not in products:
                products[pname] = []
            products[pname].append(sku)

        return render(request, "admin/bulk_stock.html", {
            "products": products,
            "title": "Bulk Stock Update",
            "opts": self.model._meta,
        })

    def stock_badge(self, obj):
        if obj.stock == 0:
            return format_html('<span class="zivo-badge zivo-red">0 — Out</span>')
        if obj.stock <= 5:
            return format_html('<span class="zivo-badge zivo-yellow">⚠ {}</span>', obj.stock)
        return format_html('<span class="zivo-badge zivo-green">✓ {}</span>', obj.stock)
    stock_badge.short_description = "Stock"

    def sold_quantity(self, obj):
        return OrderItem.objects.filter(sku=obj).aggregate(total=Sum("quantity"))["total"] or 0
    sold_quantity.short_description = "Sold"

    def save_model(self, request, obj, form, change):
        was_out_of_stock = False
        if change:
            old = SKU.objects.get(pk=obj.pk)
            was_out_of_stock = old.stock == 0
        super().save_model(request, obj, form, change)
        if was_out_of_stock and obj.stock > 0:
            self._notify_waitlist(obj.product)

    def _notify_waitlist(self, product):
        notifications = StockNotification.objects.filter(product=product)
        for n in notifications:
            last_order = Order.objects.filter(customer=n.customer).order_by("-created_at").first()
            if not last_order or not last_order.phone:
                continue
            send_whatsapp(
                last_order.phone,
                f"🎉 *{product.name}* is back in stock!\n\n🛍️ Order now:\n{settings.SITE_URL}/product/{product.id}/",
            )
        notifications.delete()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["bulk_stock_url"] = reverse("admin:store_sku_bulk_stock")
        return super().changelist_view(request, extra_context)


# ── Order ─────────────────────────────────────────────────────────────────────

@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = (
        "id", "name", "phone", "city",
        "display_status", "items_count", "display_payment",
        "total_amount", "quick_status_btn", "created_at",
    )
    list_display_links = ("id", "name", "phone", "city", "total_amount", "created_at")
    list_filter        = ("status", "payment_method", "payment_status")
    search_fields      = ("name", "phone", "razorpay_payment_id", "id")
    date_hierarchy     = "created_at"
    list_per_page      = 25
    readonly_fields = (
        "created_at", "shipped_at", "delivered_at",
        "razorpay_order_id", "razorpay_payment_id", "razorpay_signature",
    )
    inlines = [OrderItemInline]
    change_list_template = "admin/order_summary.html"
    change_form_outer_before_template = "admin/order_print_button.html"
    actions = ["export_csv", "action_confirm", "action_ship", "action_deliver", "action_cancel", "action_bulk_print"]

    class Media:
        css = {"all": ("store/admin.css",)}

    def get_urls(self):
        custom = [
            path(
                "<int:order_id>/print/",
                self.admin_site.admin_view(self.print_order_view),
                name="store_order_print",
            ),
            path(
                "<int:order_id>/quick-status/<str:new_status>/",
                self.admin_site.admin_view(self.quick_status_view),
                name="store_order_quick_status",
            ),
            path(
                "bulk-print/",
                self.admin_site.admin_view(self.bulk_print_view),
                name="store_order_bulk_print",
            ),
            # Public URL — no admin_view wrapper so non-staff get option A redirect
            path(
                "scan/<int:order_id>/",
                self.scan_order_view,
                name="store_order_scan",
            ),
        ]
        return custom + super().get_urls()

    def quick_status_view(self, request, order_id, new_status):
        """One-click status advance from the order list."""
        order = get_object_or_404(Order, id=order_id)
        allowed = {"PLACED": "CONFIRMED", "CONFIRMED": "SHIPPED", "SHIPPED": "DELIVERED"}
        if allowed.get(order.status) == new_status:
            order.status = new_status
            if new_status == "SHIPPED":
                from django.utils import timezone
                order.shipped_at = timezone.now()
                order.save()
                send_order_email(order, 'order_shipped.html', f'Your Order #{order.id} Has Been Shipped!')
            elif new_status == "DELIVERED":
                from django.utils import timezone
                order.delivered_at = timezone.now()
                order.save()
            else:
                order.save()
            messages.success(request, f"Order #{order_id} marked as {new_status}.")
        return redirect(request.META.get("HTTP_REFERER") or "..")

    def items_count(self, obj):
        n = obj.items.count()
        return format_html('<span class="zivo-badge zivo-blue">{} item{}</span>', n, "s" if n != 1 else "")
    items_count.short_description = "Items"

    def quick_status_btn(self, obj):
        next_map = {
            "PLACED":    ("CONFIRMED", "✅ Confirm",  "#f59e0b"),
            "CONFIRMED": ("SHIPPED",   "🚚 Ship",     "#8b5cf6"),
            "SHIPPED":   ("DELIVERED", "✔ Deliver",  "#10b981"),
        }
        if obj.status not in next_map:
            return "—"
        next_status, label, color = next_map[obj.status]
        url = reverse("admin:store_order_quick_status", args=[obj.pk, next_status])
        return format_html(
            '<a href="{}" style="background:{};color:#fff;padding:4px 11px;border-radius:6px;'
            'font-size:11px;font-weight:600;text-decoration:none;white-space:nowrap;">{}</a>',
            url, color, label,
        )
    quick_status_btn.short_description = "Quick Action"

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            extra_context["print_url"] = reverse("admin:store_order_print", args=[object_id])
        return super().changeform_view(request, object_id, form_url, extra_context)

    def print_order_view(self, request, order_id):
        order = get_object_or_404(Order, id=order_id)
        items = order.items.select_related("sku__product").all()
        from .utils import calculate_delivery_and_final
        subtotal = sum(item.price * item.quantity for item in items)
        delivery, total, _ = calculate_delivery_and_final(subtotal)
        scan_url = request.build_absolute_uri(
            reverse("admin:store_order_scan", args=[order_id])
        )
        return render(request, "admin/order_print.html", {
            "order":    order,
            "items":    items,
            "subtotal": subtotal,
            "delivery": delivery,
            "total":    total,
            "store":    SiteSettings.get(),
            "qr_b64":   _make_qr_b64(scan_url),
        })

    def scan_order_view(self, request, order_id):
        """Public scan endpoint. Staff → quick-update panel. Others → redirect."""
        if not (request.user.is_authenticated and request.user.is_staff):
            return redirect("/")

        order = get_object_or_404(Order, id=order_id)

        if request.method == "POST":
            new_status = request.POST.get("status")
            allowed = ["CONFIRMED", "SHIPPED", "DELIVERED", "CANCELLED"]
            if new_status in allowed:
                order.status = new_status
                if new_status == "SHIPPED":
                    from django.utils import timezone
                    order.shipped_at = timezone.now()
                    order.save()
                    send_order_email(order, 'order_shipped.html', f'Your Order #{order.id} Has Been Shipped!')
                elif new_status == "DELIVERED":
                    from django.utils import timezone
                    order.delivered_at = timezone.now()
                    order.save()
                else:
                    order.save()
                messages.success(request, f"Order #{order_id} marked as {new_status}.")
            return redirect(reverse("admin:store_order_scan", args=[order_id]))

        return render(request, "admin/order_scan.html", {"order": order})

    @admin.action(description="🖨️ Print selected invoices")
    def action_bulk_print(self, request, queryset):
        ids = ",".join(str(o.pk) for o in queryset.order_by("id"))
        return redirect(f"{reverse('admin:store_order_bulk_print')}?ids={ids}")

    def bulk_print_view(self, request):
        from .utils import calculate_delivery_and_final

        # --- resolve queryset from ?ids= OR ?from_id=&to_id= ---
        raw_ids   = request.GET.get("ids", "").strip()
        raw_from  = request.GET.get("from_id", "").strip()
        raw_to    = request.GET.get("to_id", "").strip()

        if raw_ids:
            ids = [int(i) for i in raw_ids.split(",") if i.isdigit()]
            qs  = Order.objects.filter(pk__in=ids)
        elif raw_from.isdigit() and raw_to.isdigit():
            qs  = Order.objects.filter(pk__gte=int(raw_from), pk__lte=int(raw_to))
        else:
            qs  = Order.objects.none()

        orders_data = []
        for order in qs.prefetch_related("items__sku__product").order_by("id"):
            items    = list(order.items.all())
            subtotal = sum(item.price * item.quantity for item in items)
            delivery, total, _ = calculate_delivery_and_final(subtotal)
            scan_url = request.build_absolute_uri(
                reverse("admin:store_order_scan", args=[order.pk])
            )
            orders_data.append({
                "order":    order,
                "items":    items,
                "subtotal": subtotal,
                "delivery": delivery,
                "total":    total,
                "qr_b64":   _make_qr_b64(scan_url),
            })

        return render(request, "admin/order_bulk_print.html", {
            "orders_data":    orders_data,
            "from_id":        raw_from,
            "to_id":          raw_to,
            "bulk_print_url": reverse("admin:store_order_bulk_print"),
            "store":          SiteSettings.get(),
        })

    # ── Colored badges ───────────────────────────────────────────────────────

    def display_status(self, obj):
        color_map = {
            "PLACED":    ("#3b82f6", "🔵"),
            "CONFIRMED": ("#f59e0b", "🟡"),
            "SHIPPED":   ("#8b5cf6", "🟣"),
            "DELIVERED": ("#10b981", "🟢"),
            "CANCELLED": ("#ef4444", "🔴"),
            "CREATED":   ("#9ca3af", "⚪"),
        }
        color, icon = color_map.get(obj.status, ("#6b7280", ""))
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:12px;font-size:10px;font-weight:700;white-space:nowrap;">{} {}</span>',
            color, icon, obj.status,
        )
    display_status.short_description = "Status"

    def display_payment(self, obj):
        if obj.payment_status == "PAID":
            badge = '<span class="zivo-badge zivo-green">PAID</span>'
        elif obj.payment_status == "PENDING":
            badge = '<span class="zivo-badge zivo-yellow">PENDING</span>'
        else:
            badge = '<span class="zivo-badge zivo-red">FAILED</span>'
        method = "💵 COD" if obj.payment_method == "COD" else "💳 Online"
        return format_html("{} {}", method, format_html(badge))
    display_payment.short_description = "Payment"

    def display_emails(self, obj):
        conf = '✅' if obj.confirmation_email_sent else '❌'
        ship = '✅' if obj.shipped_email_sent else '—'
        return format_html('<span title="Confirmation">📧{}</span> <span title="Shipped">🚚{}</span>', conf, ship)
    display_emails.short_description = "Emails"

    # ── CSV export ───────────────────────────────────────────────────────────

    @admin.action(description="📥 Export selected orders to CSV")
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="orders.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Order ID", "Name", "Phone", "Address", "City", "State", "Pincode",
            "Total (₹)", "Status", "Payment Method", "Payment Status",
            "Razorpay Payment ID", "Date",
        ])
        for order in queryset.order_by("-created_at"):
            writer.writerow([
                order.id, order.name, order.phone, order.address,
                order.city, order.state, order.pincode,
                order.total_amount, order.status,
                order.payment_method, order.payment_status,
                order.razorpay_payment_id or "",
                order.created_at.strftime("%Y-%m-%d %H:%M"),
            ])
        return response

    # ── Bulk status actions ──────────────────────────────────────────────────

    @admin.action(description="✅ Mark as Confirmed")
    def action_confirm(self, request, queryset):
        n = queryset.filter(status__in=["PLACED", "CREATED"]).update(status="CONFIRMED")
        self.message_user(request, f"{n} order(s) marked as Confirmed.")

    @admin.action(description="🚚 Mark as Shipped")
    def action_ship(self, request, queryset):
        n = 0
        for order in queryset.filter(status__in=["PLACED", "CONFIRMED"]):
            order.status = "SHIPPED"
            order.save()
            send_order_email(order, 'order_shipped.html', f'Your Order #{order.id} Has Been Shipped!')
            n += 1
        self.message_user(request, f"{n} order(s) marked as Shipped.")

    @admin.action(description="✔ Mark as Delivered")
    def action_deliver(self, request, queryset):
        n = 0
        for order in queryset.filter(status="SHIPPED"):
            order.status = "DELIVERED"
            order.save()
            n += 1
        self.message_user(request, f"{n} order(s) marked as Delivered.")

    @admin.action(description="❌ Cancel & Restore Stock")
    def action_cancel(self, request, queryset):
        n = 0
        for order in queryset.filter(status__in=["PLACED", "CREATED", "CONFIRMED"]):
            for item in order.items.all():
                item.sku.stock += item.quantity
                item.sku.save()
            order.status = "CANCELLED"
            order.save()
            n += 1
        self.message_user(request, f"{n} order(s) cancelled and stock restored.")

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        try:
            qs = response.context_data["cl"].queryset
        except (AttributeError, KeyError):
            return response
        response.context_data["summary"] = qs.aggregate(total=Sum("total_amount"))
        return response


# ── Order Item (analytics) ───────────────────────────────────────────────────

@admin.register(OrderItem)
class OrderItemAdmin(ModelAdmin):
    list_display = ("sku_code", "product_name", "quantity", "order_id", "order_date")
    list_filter = ("sku__product", "sku__size")
    search_fields = ("sku__sku_code", "sku__product__name")
    change_list_template = "admin/sku_analytics.html"

    def sku_code(self, obj):     return obj.sku.sku_code
    def product_name(self, obj): return obj.sku.product.name
    def order_id(self, obj):     return obj.order.id
    def order_date(self, obj):   return obj.order.created_at.date()

    sku_code.short_description = "SKU Code"
    product_name.short_description = "Product"
    order_id.short_description = "Order #"
    order_date.short_description = "Date"

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        try:
            qs = response.context_data["cl"].queryset
        except (AttributeError, KeyError):
            return response
        analytics = (
            qs.values("sku__sku_code", "sku__product__name")
            .annotate(
                total_qty=Sum("quantity"),
                total_orders=Count("order", distinct=True),
                revenue=Sum(
                    ExpressionWrapper(
                        F("quantity") * F("sku__selling_price"),
                        output_field=DecimalField(),
                    )
                ),
            )
            .order_by("-total_qty")[:10]
        )
        response.context_data["analytics"] = analytics
        return response


# ── Stock Notification ────────────────────────────────────────────────────────

@admin.register(StockNotification)
class StockNotificationAdmin(ModelAdmin):
    list_display = ("id", "product", "customer_name", "customer_phone", "created_at")
    list_filter = ("product", "created_at")
    search_fields = ("product__name", "customer__name", "customer__phone")
    readonly_fields = ("created_at",)

    def customer_name(self, obj):
        return obj.customer.name
    customer_name.short_description = "Customer"

    def customer_phone(self, obj):
        return obj.customer.phone
    customer_phone.short_description = "Phone"


# ── Customer ──────────────────────────────────────────────────────────────────

@admin.register(Customer)
class CustomerAdmin(ModelAdmin):
    list_display  = ("id", "name", "phone", "email", "order_count", "date_joined")
    search_fields = ("name", "phone", "email")
    readonly_fields = ("date_joined", "password")
    ordering = ("-date_joined",)

    def order_count(self, obj):
        return obj.orders.count()
    order_count.short_description = "Orders"


# ── Admin Users (Staff) ───────────────────────────────────────────────────────

admin.site.unregister(User)

@admin.register(User)
class StaffUserAdmin(ModelAdmin, DjangoUserAdmin):
    """Simplified User admin — focused on creating staff accounts with roles."""

    add_form = StaffUserCreationForm

    list_display  = ("username", "email", "is_active", "is_staff", "role_list", "last_login")
    list_filter   = ("is_active", "is_staff", "groups")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering      = ("-date_joined",)

    # Change form: clean sections, no granular permissions
    fieldsets = (
        ("Login", {"fields": ("username", "password")}),
        ("Personal Info", {"fields": ("first_name", "last_name", "email")}),
        ("Role & Access", {
            "fields": ("is_active", "is_staff", "groups"),
            "description": (
                "Pick a <strong>Role</strong> from the groups list to grant pre-set permissions. "
                "Never enable Superuser unless this person is a co-owner."
            ),
        }),
    )

    # Add form: credentials first, then role
    add_fieldsets = (
        ("Login Credentials", {
            "classes": ("wide",),
            "fields": ("username", "password1", "password2"),
        }),
        ("Role & Access", {
            "fields": ("is_staff", "groups"),
        }),
    )

    def get_fieldsets(self, request, obj=None):
        # Superusers also see the is_superuser toggle
        access_fields = ["is_active", "is_staff", "groups"]
        if request.user.is_superuser:
            access_fields.append("is_superuser")

        if obj is None:
            # Add form
            return (
                ("Login Credentials", {
                    "classes": ("wide",),
                    "fields": ("username", "password1", "password2"),
                }),
                ("Role & Access", {
                    "fields": tuple(f for f in access_fields if f not in ("is_active",)),
                }),
            )

        return (
            ("Login", {"fields": ("username", "password")}),
            ("Personal Info", {"fields": ("first_name", "last_name", "email")}),
            ("Role & Access", {
                "fields": tuple(access_fields),
                "description": (
                    "Assign a <strong>Role</strong> (group) to grant pre-set permissions. "
                    "Only enable <em>Superuser</em> for a co-owner."
                ),
            }),
        )

    def role_list(self, obj):
        groups = obj.groups.all()
        if not groups:
            return format_html('<span style="color:#9ca3af;">No role</span>')
        badges = " ".join(
            f'<span style="background:#8b5cf6;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;">{g.name}</span>'
            for g in groups
        )
        return format_html(badges)
    role_list.short_description = "Role"

    class Media:
        css = {"all": ("store/admin.css",)}


# ── Site Settings (singleton) ─────────────────────────────────────────────────

@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    """Only one row ever exists — no add/delete, just edit."""

    fieldsets = (
        ("Payment Options", {
            "fields": ("cod_enabled",),
            "description": (
                "Toggle <strong>Cash on Delivery</strong> for the entire store. "
                "When disabled, customers can only pay online."
            ),
        }),
        ("Delivery Pricing", {
            "fields": ("delivery_charge", "free_delivery_min_order"),
            "description": (
                "Set the flat delivery fee and the cart total above which delivery becomes free."
            ),
        }),
        ("Store Identity", {
            "fields": ("store_name", "store_phone", "store_address"),
            "description": (
                "These details appear as the <strong>FROM</strong> section on every "
                "invoice and shipping label."
            ),
        }),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Redirect straight to the edit page — no need for a list
        obj, _ = SiteSettings.objects.get_or_create(pk=1)
        return redirect(reverse("admin:store_sitesettings_change", args=[obj.pk]))


# ── Coupon ────────────────────────────────────────────────────────────────────

@admin.register(Coupon)
class CouponAdmin(ModelAdmin):
    list_display = ("code", "discount_amount", "min_order", "is_active", "used_count", "usage_limit", "valid_from", "valid_to")
    list_filter = ("is_active", "one_per_customer")
    search_fields = ("code",)
    list_editable = ("is_active",)


# ── Offer ─────────────────────────────────────────────────────────────────────

@admin.register(Offer)
class OfferAdmin(ModelAdmin):
    list_display  = ("name", "offer_type", "discount_summary", "scope_summary", "is_active", "valid_from", "valid_to")
    list_filter   = ("offer_type", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name", "description")
    filter_horizontal = ("applicable_products", "applicable_categories")

    fieldsets = (
        ("Offer Details", {
            "fields": ("name", "offer_type", "description", "is_active", "valid_from", "valid_to"),
        }),
        ("Discount Configuration", {
            "fields": ("discount_percent", "buy_quantity", "get_quantity", "get_discount_percent", "min_quantity"),
            "description": (
                "<strong>Percentage / Min Qty:</strong> set <em>Discount %</em>. &nbsp;"
                "<strong>Buy X Get Y:</strong> set <em>Buy qty</em>, <em>Get qty</em>, <em>Get discount %</em>. &nbsp;"
                "<strong>BOGO:</strong> no extra config needed. &nbsp;"
                "<strong>Min Qty:</strong> set <em>Min quantity</em> and <em>Discount %</em>."
            ),
        }),
        ("Scope — leave both empty to apply to the entire cart", {
            "fields": ("applicable_products", "applicable_categories"),
        }),
    )

    def discount_summary(self, obj):
        t = obj.offer_type
        if t == "PERCENTAGE":
            return format_html("<span>{}</span>", f"{obj.discount_percent}% off")
        if t == "BOGO":
            return format_html("<span>Buy 1 Get 1 Free</span>")
        if t == "BUY_X_GET_Y":
            return format_html(
                "<span>Buy {} Get {} at {}% off</span>",
                obj.buy_quantity, obj.get_quantity, obj.get_discount_percent,
            )
        if t == "MIN_QTY":
            return format_html(
                "<span>Buy {}+, get {}% off</span>",
                obj.min_quantity, obj.discount_percent,
            )
        return "—"
    discount_summary.short_description = "Discount"

    def scope_summary(self, obj):
        prods = obj.applicable_products.count()
        cats  = obj.applicable_categories.count()
        if not prods and not cats:
            return "Entire cart"
        parts = []
        if prods:
            parts.append(f"{prods} product{'s' if prods > 1 else ''}")
        if cats:
            parts.append(f"{cats} categor{'ies' if cats > 1 else 'y'}")
        return ", ".join(parts)
    scope_summary.short_description = "Scope"

    class Media:
        js = ("store/offer_admin.js",)


# ── Announcement ──────────────────────────────────────────────────────────────

@admin.register(Announcement)
class AnnouncementAdmin(ModelAdmin):
    list_display  = ("text", "is_active", "valid_from", "valid_to")
    list_editable = ("is_active",)
    list_filter   = ("is_active",)


# ── Review ────────────────────────────────────────────────────────────────────

@admin.register(Review)
class ReviewAdmin(ModelAdmin):
    list_display = ("customer", "product", "rating", "title", "created_at")
    list_filter = ("rating",)
    search_fields = ("customer__name", "product__name", "title")
    readonly_fields = ("customer", "product", "order_item", "rating", "title", "comment", "created_at")


# ── Return Requests ───────────────────────────────────────────────────────────

class ReturnItemInline(TabularInline):
    model = ReturnItem
    extra = 0
    readonly_fields = ("order_item", "quantity")
    can_delete = False


@admin.register(ReturnRequest)
class ReturnRequestAdmin(ModelAdmin):
    list_display = ("id", "display_order", "display_customer", "reason", "display_status", "refund_amount", "created_at")
    list_filter = ("status", "reason", "created_at")
    search_fields = ("order__id", "order__name", "order__phone")
    readonly_fields = ("order", "reason", "reason_detail", "display_video", "refund_via", "upi_id", "bank_account_name", "bank_account_number", "bank_ifsc", "created_at", "updated_at", "razorpay_refund_id")
    inlines = [ReturnItemInline]
    actions = ["action_approve", "action_reject", "action_process_refund"]

    fieldsets = (
        ("Return Info", {"fields": ("order", "reason", "reason_detail", "display_video", "created_at")}),
        ("COD Refund Details", {"fields": ("refund_via", "upi_id", "bank_account_name", "bank_account_number", "bank_ifsc"),
                                "description": "Filled by customer for COD orders only."}),
        ("Resolution", {"fields": ("status", "admin_notes", "refund_amount", "razorpay_refund_id", "updated_at")}),
    )

    def display_order(self, obj):
        url = reverse("admin:store_order_change", args=[obj.order_id])
        return format_html('<a href="{}">Order #{}</a>', url, obj.order_id)
    display_order.short_description = "Order"

    def display_customer(self, obj):
        return obj.order.name
    display_customer.short_description = "Customer"

    def display_video(self, obj):
        if obj.unboxing_video:
            return format_html('<a href="{}" target="_blank">▶ View Unboxing Video</a>', obj.unboxing_video)
        return "—"
    display_video.short_description = "Unboxing Video"

    def display_status(self, obj):
        colors = {
            "REQUESTED":        ("#fef9c3", "#854d0e"),
            "APPROVED":         ("#dcfce7", "#166534"),
            "REJECTED":         ("#fee2e2", "#991b1b"),
            "REFUND_PROCESSED": ("#ede9fe", "#5b21b6"),
        }
        bg, fg = colors.get(obj.status, ("#f3f4f6", "#374151"))
        return format_html(
            '<span style="background:{};color:{};padding:3px 10px;border-radius:12px;font-size:0.78rem;font-weight:600;">{}</span>',
            bg, fg, obj.get_status_display()
        )
    display_status.short_description = "Status"

    def save_model(self, request, obj, form, change):
        """Send email whenever status changes via manual save in detail view."""
        if change and "status" in form.changed_data:
            super().save_model(request, obj, form, change)
            from .views import _send_return_email
            if obj.status == "APPROVED":
                _send_return_email(obj, "return_update.html", f"Return Request #{obj.id} Approved — Zivo")
            elif obj.status == "REJECTED":
                _send_return_email(obj, "return_update.html", f"Return Request #{obj.id} Update — Zivo")
            elif obj.status == "REFUND_PROCESSED":
                _send_return_email(obj, "return_update.html", f"Refund Processed for Return #{obj.id} — Zivo")
        else:
            super().save_model(request, obj, form, change)

    @admin.action(description="Approve selected return requests")
    def action_approve(self, request, queryset):
        from .views import _send_return_email
        updated = 0
        for rr in queryset.exclude(status="APPROVED"):
            rr.status = "APPROVED"
            rr.save()
            _send_return_email(rr, "return_update.html", f"Return Request #{rr.id} Approved — Zivo")
            updated += 1
        self.message_user(request, f"{updated} return request(s) approved.")

    @admin.action(description="Reject selected return requests")
    def action_reject(self, request, queryset):
        from .views import _send_return_email
        updated = 0
        for rr in queryset.exclude(status="REJECTED"):
            rr.status = "REJECTED"
            rr.save()
            _send_return_email(rr, "return_update.html", f"Return Request #{rr.id} Update — Zivo")
            updated += 1
        self.message_user(request, f"{updated} return request(s) rejected.")

    @admin.action(description="Mark refund processed (online orders via Razorpay)")
    def action_process_refund(self, request, queryset):
        import razorpay
        from .views import _send_return_email
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        for rr in queryset.exclude(status="REFUND_PROCESSED"):
            order = rr.order
            refund_amount = rr.refund_amount or order.total_amount
            if order.payment_method == "ONLINE" and order.razorpay_payment_id:
                try:
                    resp = client.payment.refund(order.razorpay_payment_id, {
                        "amount": int(refund_amount * 100),  # paise
                        "speed": "optimum",
                        "notes": {"return_request_id": str(rr.id)},
                    })
                    rr.razorpay_refund_id = resp.get("id", "")
                except Exception as e:
                    self.message_user(request, f"Razorpay refund failed for Return #{rr.id}: {e}", level="error")
                    continue
            rr.status = "REFUND_PROCESSED"
            rr.refund_amount = refund_amount
            rr.save()
            _send_return_email(rr, "return_update.html", f"Refund Processed for Return #{rr.id} — Zivo")
        self.message_user(request, "Refund(s) processed.")
