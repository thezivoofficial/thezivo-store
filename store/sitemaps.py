from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Product, Category


class ProductSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Product.objects.filter(active=True)

    def location(self, product):
        return reverse("product_detail", args=[product.id])


class CategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return Category.objects.filter(is_active=True, gender__in=["men", "women"])

    def location(self, cat):
        return f"/{cat.gender}/{cat.slug}/"


class StaticSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return ["home", "men", "women"]

    def location(self, name):
        return reverse(name)
