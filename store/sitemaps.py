from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Product


class ProductSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Product.objects.filter(active=True)

    def location(self, product):
        return reverse("product_detail", args=[product.id])


class StaticSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return [
            "home", "men", "women",
            "login", "signup",
        ]

    def location(self, name):
        return reverse(name)
