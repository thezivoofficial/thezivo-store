from django.db import models
from django.contrib.auth.hashers import make_password, check_password as _check_password
from io import BytesIO
from django.core.files.base import ContentFile
from PIL import Image as PilImage


def compress_image(image_field, max_width=1200, quality=85):
    """Resize and compress an ImageField in-place. No-op if already small."""
    try:
        img = PilImage.open(image_field)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), PilImage.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        image_field.save(
            image_field.name.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ".jpg",
            ContentFile(buf.getvalue()),
            save=False,
        )
    except Exception:
        pass  # never break a save due to compression failure


class Customer(models.Model):
    phone       = models.CharField(max_length=15, unique=True)
    name        = models.CharField(max_length=150)
    email       = models.EmailField(blank=True)
    password    = models.CharField(max_length=128)
    is_active   = models.BooleanField(default=True)
    date_joined = models.DateTimeField(auto_now_add=True)

    def set_password(self, raw):
        self.password = make_password(raw)

    def check_password(self, raw):
        return _check_password(raw, self.password)

    def __str__(self):
        return f"{self.name} ({self.phone})"

    class Meta:
        ordering = ["-date_joined"]
        verbose_name = "Customer"


class Category(models.Model):
    name       = models.CharField(max_length=100)
    slug       = models.SlugField(max_length=50, unique=True, help_text="URL key used in links, e.g. 'tshirts'")
    gender     = models.CharField(
        max_length=10,
        choices=[("men", "Men"), ("women", "Women"), ("unisex", "Unisex")],
        blank=True, default="",
        help_text="Which nav dropdown this appears under. Leave blank to hide from nav.",
    )
    image      = models.ImageField(upload_to="categories/", null=True, blank=True, max_length=255,
                                   help_text="Shown on the home page category tiles")
    size_chart = models.ImageField(upload_to="size_charts/", null=True, blank=True, max_length=255,
                                   help_text="Size chart image shown on the product page (upload from Canva/Photoshop)")
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Lower number appears first in nav")
    is_active  = models.BooleanField(default=True)

    class Meta:
        ordering = ["gender", "sort_order", "name"]
        verbose_name = "Category"
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class Product(models.Model):
    GENDER_CHOICES = (
        ("men", "Men"),
        ("women", "Women"),
        ("unisex", "Unisex"),
    )

    name        = models.CharField(max_length=200)
    gender      = models.CharField(max_length=10, choices=GENDER_CHOICES, db_index=True)
    category    = models.ForeignKey(
        "Category",
        on_delete=models.PROTECT,
        related_name="products",
        db_index=True,
        null=True, blank=True,
    )
    image       = models.ImageField(upload_to="products/")
    active      = models.BooleanField(default=True, db_index=True)
    is_trending = models.BooleanField(default=False, db_index=True)
    brand       = models.CharField(max_length=100)
    description = models.TextField(
        blank=True, default="",
        help_text="Shown on the product page. Supports basic HTML (bold, bullet lists).",
    )
    material    = models.CharField(
        max_length=200, blank=True, default="",
        help_text='e.g. "100% Cotton" or "60% Cotton, 40% Polyester"',
    )
    care        = models.CharField(
        max_length=300, blank=True, default="",
        help_text='e.g. "Machine wash cold, do not tumble dry"',
    )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.image:
            compress_image(self.image)

    def __str__(self):
        return self.name

    @property
    def avg_rating(self):
        from django.db.models import Avg
        result = self.reviews.aggregate(avg=Avg("rating"))["avg"]
        return round(result, 1) if result is not None else None

    @property
    def review_count(self):
        return self.reviews.count()

    @property
    def prefetched_stock(self):
        """Sum of stock across prefetched sku_set. Used when sku_set is prefetched (home page)."""
        return sum(sku.stock for sku in self.sku_set.all())

    class Meta:
        ordering = ["-id"]
        verbose_name_plural = "Products"
    
class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images"
    )
    image = models.ImageField(upload_to="products/extra/")
    color = models.CharField(max_length=100, blank=True, default="", help_text="Tag this image to a color variant (e.g. Red). Leave blank for shared images.")
    is_primary = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.image:
            compress_image(self.image)

    def __str__(self):
        return f"Image - {self.product.name}"


from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


# ── Cloudinary cleanup ────────────────────────────────────────────────────────

@receiver(post_delete, sender=ProductImage)
def delete_product_image_from_cloudinary(sender, instance, **kwargs):
    if instance.image:
        try:
            import cloudinary.uploader
            public_id = instance.image.name.rsplit(".", 1)[0]
            cloudinary.uploader.destroy(public_id)
        except Exception as e:
            print(f"[CLOUDINARY] Failed to delete image {instance.image.name}: {e}")


# ── Cache invalidation ────────────────────────────────────────────────────────

def _clear_product_cache(product_id):
    try:
        from django.core.cache import cache
        cache.delete(f"product_detail_{product_id}")
        cache.delete("home_page_data")
    except Exception:
        pass


@receiver(post_save, sender="store.Product")
def on_product_save(sender, instance, **kwargs):
    _clear_product_cache(instance.id)


@receiver(post_delete, sender="store.Product")
def on_product_delete(sender, instance, **kwargs):
    _clear_product_cache(instance.id)


@receiver(post_save, sender="store.ProductImage")
def on_product_image_save(sender, instance, **kwargs):
    _clear_product_cache(instance.product_id)


@receiver(post_save, sender="store.SKU")
def on_sku_save(sender, instance, **kwargs):
    _clear_product_cache(instance.product_id)


@receiver(post_save, sender="store.SiteSettings")
def on_site_settings_save(sender, instance, **kwargs):
    try:
        from django.core.cache import cache
        cache.delete("home_page_data")
    except Exception:
        pass


class SKU(models.Model):
    sku_code = models.CharField(
        max_length=20,
        unique=True
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    size = models.CharField(max_length=10)
    color = models.CharField(max_length=50)
    mrp = models.PositiveIntegerField()
    selling_price = models.PositiveIntegerField()
    stock = models.PositiveIntegerField(default=0)
    weight_grams = models.PositiveIntegerField(default=200, help_text="Weight in grams (used for shipping)")
    created_at = models.DateTimeField(auto_now_add=True)
    
    @property
    def discount_percent(self):
        if self.mrp > self.selling_price:
            return int(((self.mrp - self.selling_price) / self.mrp) * 100)
        return 0

    def __str__(self):
        return f"{self.product.name} - {self.size} - {self.color}"
    
    class Meta:
        verbose_name = "SKU"
        ordering = ["product", "size"]


class Order(models.Model):

    customer = models.ForeignKey(
        "Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    
    def save(self, *args, **kwargs):
        from django.utils import timezone
        if self.pk:
            old = Order.objects.filter(pk=self.pk).first()
            if old:
                if old.status != "SHIPPED" and self.status == "SHIPPED":
                    if not self.shipped_at:
                        self.shipped_at = timezone.now()
                if old.status != "DELIVERED" and self.status == "DELIVERED":
                    if not self.delivered_at:
                        self.delivered_at = timezone.now()
        super().save(*args, **kwargs)

    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    address = models.TextField()
    city = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=100, blank=True, default="")
    pincode = models.CharField(max_length=10, blank=True, default="")
    delivery_instructions = models.CharField(max_length=500, blank=True, default="")
    guest_email = models.EmailField(blank=True, default="")

    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    @property
    def items_subtotal(self):
        """Sum of item prices only (excludes delivery). Uses prefetch cache if available."""
        return sum(item.price * item.quantity for item in self.items.all())

    @property
    def delivery_charge(self):
        """Delivery charge = total_amount minus items subtotal."""
        return self.total_amount - self.items_subtotal

    @property
    def contact_email(self):
        """Email for order communications: guest_email, or customer.email if logged in."""
        return self.guest_email or (self.customer.email if self.customer else "")

    @property
    def invoice_number(self):
        """Human-readable invoice number: INV-YYYY-XXXXX"""
        return f"INV-{self.created_at.strftime('%Y')}-{self.id:05d}"
    
    status = models.CharField(
        max_length=20,
        choices=[
            ("PLACED", "Placed"),
            ("CONFIRMED", "Confirmed"),
            ("SHIPPED", "Shipped"),
            ("DELIVERED", "Delivered"),
            ("CANCELLED", "Cancelled"),
        ],
        default="PLACED"
    )
    
    payment_method = models.CharField(
        max_length=10,
        choices=[
            ("COD", "Cash on Delivery"),
            ("ONLINE", "Online Payment")
        ]
    )
    
    payment_status = models.CharField(
        max_length=20,
        choices=[
            ("PENDING", "Pending"),
            ("PAID", "Paid"),
            ("FAILED", "Failed"),
        ],
        default="PENDING"
    )
    
    # 🔹 Razorpay fields
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=200, blank=True, null=True)

    # 🔹 Courier / tracking
    awb_number   = models.CharField(max_length=100, blank=True)
    courier_name = models.CharField(max_length=100, blank=True)
    tracking_url = models.CharField(max_length=500, blank=True)

    coupon          = models.ForeignKey('Coupon', null=True, blank=True, on_delete=models.SET_NULL)
    discount_amount = models.PositiveIntegerField(default=0)   # coupon discount
    offer_discount  = models.PositiveIntegerField(default=0)   # auto-offer discount

    confirmation_email_sent = models.BooleanField(default=False)
    shipped_email_sent      = models.BooleanField(default=False)
    delivered_email_sent    = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    
    
    def __str__(self):
        return f"Order #{self.id} | {self.payment_method} | {self.payment_status}"
    
    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Orders"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    sku = models.ForeignKey(SKU, on_delete=models.CASCADE)
    quantity = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    
    class Meta:
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"
    
    
class ReturnRequest(models.Model):
    REASON_CHOICES = [
        ("WRONG_ITEM", "Wrong item delivered"),
        ("DAMAGED",    "Item arrived damaged"),
    ]
    STATUS_CHOICES = [
        ("REQUESTED",        "Requested"),
        ("APPROVED",         "Approved"),
        ("REJECTED",         "Rejected"),
        ("REFUND_PROCESSED", "Refund Processed"),
    ]

    order          = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="return_request")
    reason         = models.CharField(max_length=20, choices=REASON_CHOICES)
    reason_detail  = models.TextField(blank=True, default="")
    unboxing_video = models.URLField(
        max_length=500,
        blank=True, default="",
        help_text="Cloudinary URL of the unboxing video uploaded by customer.",
    )
    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default="REQUESTED")
    admin_notes    = models.TextField(blank=True, default="", help_text="Internal notes (not shown to customer).")
    refund_amount  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    razorpay_refund_id = models.CharField(max_length=100, blank=True, default="")

    # COD refund details (filled by customer during return request)
    refund_via          = models.CharField(max_length=10, blank=True, default="",
                            choices=[("UPI", "UPI"), ("BANK", "Bank Transfer")],
                            help_text="Preferred refund method for COD orders.")
    upi_id              = models.CharField(max_length=100, blank=True, default="")
    bank_account_name   = models.CharField(max_length=100, blank=True, default="")
    bank_account_number = models.CharField(max_length=20, blank=True, default="")
    bank_ifsc           = models.CharField(max_length=15, blank=True, default="")

    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Return Request"
        verbose_name_plural = "Return Requests"

    def __str__(self):
        return f"Return #{self.id} — Order #{self.order_id} [{self.status}]"


class ReturnItem(models.Model):
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="return_items")
    order_item     = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="return_items")
    quantity       = models.PositiveIntegerField()

    class Meta:
        verbose_name = "Return Item"
        verbose_name_plural = "Return Items"

    def __str__(self):
        return f"{self.order_item.sku} x{self.quantity}"


class StockNotification(models.Model):
    customer = models.ForeignKey("Customer", on_delete=models.CASCADE, null=True, blank=True)
    product  = models.ForeignKey(Product, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("customer", "product")
        ordering = ["-created_at"]
        verbose_name = "Stock Notification"

    def __str__(self):
        return f"{self.customer} → {self.product}"
    
class Address(models.Model):
    customer = models.ForeignKey("Customer", on_delete=models.CASCADE, related_name="addresses", null=True, blank=True)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    address = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=10)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.name} - {self.city}"

    def save(self, *args, **kwargs):
        if self.is_default:
            Address.objects.filter(
                customer=self.customer, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class SiteSettings(models.Model):
    """Singleton model — only one row ever exists. Controls store-wide toggles."""
    cod_enabled = models.BooleanField(
        default=True,
        verbose_name="Cash on Delivery enabled",
        help_text="Uncheck to disable COD and force all customers to pay online.",
    )

    # Delivery pricing
    delivery_charge = models.PositiveIntegerField(
        default=59,
        verbose_name="Delivery charge (₹)",
        help_text="Flat delivery fee charged when order is below the free-delivery threshold.",
    )
    free_delivery_min_order = models.PositiveIntegerField(
        default=799,
        verbose_name="Free delivery above (₹)",
        help_text="Orders at or above this amount get free delivery.",
    )

    # Store identity — printed on invoices & shipping labels
    store_name    = models.CharField(max_length=100, default="Zivo Fashion Store")
    store_phone   = models.CharField(max_length=20, blank=True, default="")
    store_address = models.TextField(
        blank=True, default="",
        help_text="Full return/pickup address printed on shipping labels.",
    )

    # Returns
    return_window_days = models.PositiveIntegerField(
        default=7,
        verbose_name="Return window (days)",
        help_text="Number of days after delivery within which customers can request a return.",
    )

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return "Site Settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class CartItem(models.Model):
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="cart_items"
    )
    sku = models.ForeignKey(SKU, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("customer", "sku")
        verbose_name = "Cart Item"
        verbose_name_plural = "Cart Items"

    def __str__(self):
        return f"{self.customer} — {self.sku} x{self.quantity}"


class WishlistItem(models.Model):
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="wishlist_items"
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    color = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("customer", "product", "color")
        verbose_name = "Wishlist Item"
        verbose_name_plural = "Wishlist Items"

    def __str__(self):
        return f"{self.customer} — {self.product}"


class Announcement(models.Model):
    """Free-text banner messages — shown in the site-wide ticker alongside coupon codes."""
    text       = models.CharField(max_length=200, help_text="e.g. 'Free shipping this weekend!' or 'Buy 2 Get 1 Free on all T-Shirts'")
    is_active  = models.BooleanField(default=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to   = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.text[:60]


class Coupon(models.Model):
    code             = models.CharField(max_length=20, unique=True, db_index=True)
    discount_amount  = models.PositiveIntegerField(help_text="Flat ₹ discount applied to order total")
    min_order        = models.PositiveIntegerField(default=0, help_text="Minimum cart subtotal required")
    is_active        = models.BooleanField(default=True)
    valid_from       = models.DateField(null=True, blank=True)
    valid_to         = models.DateField(null=True, blank=True)
    usage_limit      = models.PositiveIntegerField(null=True, blank=True, help_text="Leave blank for unlimited")
    used_count       = models.PositiveIntegerField(default=0)
    one_per_customer = models.BooleanField(default=False, help_text="Each customer can use this only once")
    show_in_banner   = models.BooleanField(default=False, help_text="Show this coupon in the site-wide announcement banner")

    def is_valid(self):
        from django.utils import timezone
        today = timezone.now().date()
        if not self.is_active:
            return False, "This coupon is inactive."
        if self.valid_from and today < self.valid_from:
            return False, "This coupon is not yet valid."
        if self.valid_to and today > self.valid_to:
            return False, "This coupon has expired."
        if self.usage_limit is not None and self.used_count >= self.usage_limit:
            return False, "This coupon has reached its usage limit."
        return True, "OK"

    class Meta:
        verbose_name = "Coupon"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.code} (₹{self.discount_amount} off)"


class Offer(models.Model):
    """Auto-applied promotions — no promo code needed. Applied at cart/checkout automatically."""

    PERCENTAGE  = 'PERCENTAGE'
    BOGO        = 'BOGO'
    BUY_X_GET_Y = 'BUY_X_GET_Y'
    MIN_QTY     = 'MIN_QTY'

    OFFER_TYPES = [
        (PERCENTAGE,  'Percentage Discount — X% off matching items'),
        (BOGO,        'Buy 1 Get 1 Free — cheapest matching item is free'),
        (BUY_X_GET_Y, 'Buy X Get Y — buy X, get Y items at a discount'),
        (MIN_QTY,     'Minimum Quantity — buy min qty, get X% off'),
    ]

    name        = models.CharField(max_length=100)
    offer_type  = models.CharField(max_length=20, choices=OFFER_TYPES)
    description = models.CharField(
        max_length=255, blank=True,
        help_text="Short label shown to the customer on cart and checkout pages. "
                  "Leave blank to use the offer name.",
    )
    is_active  = models.BooleanField(default=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to   = models.DateField(null=True, blank=True)

    # ── PERCENTAGE / MIN_QTY ───────────────────────────────────────────
    discount_percent = models.PositiveIntegerField(
        default=0,
        help_text="Discount % (1–100). Used for Percentage and Min Quantity offer types.",
    )

    # ── BUY_X_GET_Y ───────────────────────────────────────────────────
    buy_quantity = models.PositiveIntegerField(
        default=1,
        help_text="Number of items the customer must buy. (BUY X GET Y only)",
    )
    get_quantity = models.PositiveIntegerField(
        default=1,
        help_text="Number of items the customer gets at a discount. (BUY X GET Y only)",
    )
    get_discount_percent = models.PositiveIntegerField(
        default=100,
        help_text="Discount % on the 'get' items — 100 means completely free. (BUY X GET Y only)",
    )

    # ── MIN_QTY ───────────────────────────────────────────────────────
    min_quantity = models.PositiveIntegerField(
        default=2,
        help_text="Minimum number of matching items needed to trigger the discount. (Min Quantity only)",
    )

    # ── Scope — both empty = entire cart ──────────────────────────────
    applicable_products   = models.ManyToManyField(
        'Product', blank=True, related_name='offers',
        verbose_name="Applicable Products",
        help_text="Restrict this offer to specific products. Leave empty to apply to all.",
    )
    applicable_categories = models.ManyToManyField(
        'Category', blank=True, related_name='offers',
        verbose_name="Applicable Categories",
        help_text="Restrict this offer to specific categories. Leave empty to apply to all.",
    )

    class Meta:
        ordering = ['name']
        verbose_name = "Offer"
        verbose_name_plural = "Offers"

    def __str__(self):
        return self.name

    def is_valid(self):
        from django.utils import timezone
        today = timezone.localdate()
        if not self.is_active:
            return False
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_to and today > self.valid_to:
            return False
        return True


class Review(models.Model):
    customer   = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="reviews")
    product    = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    order_item = models.ForeignKey(OrderItem, null=True, blank=True, on_delete=models.SET_NULL)
    rating     = models.PositiveSmallIntegerField()  # 1–5
    title      = models.CharField(max_length=120, blank=True)
    comment    = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("customer", "product")
        ordering = ["-created_at"]
        verbose_name = "Review"

    def __str__(self):
        return f"{self.customer.name} → {self.product.name} ({self.rating}★)"


class NewsletterSubscriber(models.Model):
    email         = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    is_active     = models.BooleanField(default=True)
    token         = models.CharField(max_length=64, unique=True, blank=True)

    class Meta:
        ordering = ["-subscribed_at"]
        verbose_name = "Newsletter Subscriber"
        verbose_name_plural = "Newsletter Subscribers"

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class PasswordResetOTP(models.Model):
    phone      = models.CharField(max_length=15)
    otp        = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used    = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Password Reset OTP"

    def is_valid(self):
        import datetime
        from django.utils import timezone
        expiry = self.created_at + datetime.timedelta(minutes=10)
        return not self.is_used and timezone.now() < expiry

    def __str__(self):
        return f"OTP for {self.phone}"


class SearchTerm(models.Model):
    term         = models.CharField(max_length=200, unique=True)
    count        = models.PositiveIntegerField(default=1)
    last_searched = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-count"]
        verbose_name = "Search Term"

    def __str__(self):
        return f"{self.term} ({self.count})"


class AbandonedCart(models.Model):
    customer       = models.OneToOneField("Customer", on_delete=models.CASCADE, related_name="abandoned_cart")
    updated_at     = models.DateTimeField(auto_now=True)
    email_sent     = models.BooleanField(default=False)
    items_snapshot = models.JSONField(default=list)

    class Meta:
        verbose_name = "Abandoned Cart"

    def __str__(self):
        return f"Cart – {self.customer} ({self.updated_at:%Y-%m-%d %H:%M})"


class StoreCredit(models.Model):
    """One balance row per customer. Issued when a size-exchange replacement is out of stock."""
    customer   = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name="store_credit")
    balance    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Store Credit"
        verbose_name_plural = "Store Credits"

    def __str__(self):
        return f"{self.customer} — ₹{self.balance}"

    @classmethod
    def get_balance(cls, customer):
        obj, _ = cls.objects.get_or_create(customer=customer)
        return obj.balance

    def add(self, amount):
        self.balance += amount
        self.save()

    def deduct(self, amount):
        """Deduct up to `amount` from balance. Returns amount actually deducted."""
        actual = min(amount, self.balance)
        self.balance -= actual
        self.save()
        return actual


class SizeExchangeRequest(models.Model):
    STATUS_CHOICES = [
        ("REQUESTED",           "Requested"),
        ("APPROVED",            "Approved — Replacement Shipping"),
        ("REJECTED",            "Rejected"),
        ("STORE_CREDIT_ISSUED", "Store Credit Issued (size OOS)"),
        ("COMPLETED",           "Completed"),
    ]

    order         = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="exchange_requests")
    order_item    = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="exchange_requests")
    requested_sku = models.ForeignKey(
        SKU, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="exchange_requests_incoming",
        help_text="The new size/SKU the customer wants.",
    )
    status        = models.CharField(max_length=25, choices=STATUS_CHOICES, default="REQUESTED")
    admin_notes   = models.TextField(blank=True, default="", help_text="Internal notes (not shown to customer).")
    credit_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Store credit issued if requested size was out of stock.",
    )
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Size Exchange Request"
        verbose_name_plural = "Size Exchange Requests"

    def __str__(self):
        return f"Exchange #{self.id} — Order #{self.order_id} [{self.status}]"


# ── In-app notifications ───────────────────────────────────────────────────────

class UserNotification(models.Model):
    TYPE_CHOICES = [
        ("ORDER",  "Order Update"),
        ("PROMO",  "Promotion"),
        ("SYSTEM", "System"),
    ]
    customer   = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="notifications")
    title      = models.CharField(max_length=120)
    message    = models.CharField(max_length=300)
    notif_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="ORDER")
    link       = models.CharField(max_length=200, blank=True, default="")
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "User Notification"
        verbose_name_plural = "User Notifications"

    def __str__(self):
        return f"{self.customer} — {self.title}"


# ── Browser push subscriptions ─────────────────────────────────────────────────

class PushSubscription(models.Model):
    customer   = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint   = models.TextField(unique=True)
    p256dh     = models.TextField()
    auth       = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Push Subscription"
        verbose_name_plural = "Push Subscriptions"

    def __str__(self):
        return f"{self.customer} — {self.endpoint[:60]}"


# ── Site banners (popup / toast) ───────────────────────────────────────────────

class SiteBanner(models.Model):
    TYPE_CHOICES = [
        ("INFO",    "Info (blue)"),
        ("PROMO",   "Promo (purple)"),
        ("URGENT",  "Urgent (red)"),
        ("SUCCESS", "Success (green)"),
    ]
    title       = models.CharField(max_length=100)
    message     = models.CharField(max_length=300)
    banner_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="PROMO")
    link        = models.CharField(max_length=200, blank=True, default="", help_text="Optional CTA URL")
    link_text   = models.CharField(max_length=50, blank=True, default="", help_text="CTA button label")
    is_active   = models.BooleanField(default=True)
    valid_from  = models.DateTimeField(null=True, blank=True)
    valid_to    = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Site Banner"
        verbose_name_plural = "Site Banners"

    def __str__(self):
        return f"[{self.banner_type}] {self.title}"