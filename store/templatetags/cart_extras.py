from django import template

register = template.Library()

@register.filter
def multiply(a, b):
    return a * b

@register.filter
def subtract(value, arg):
    return value - arg


@register.filter
def dict_get(d, key):
    """Look up a dict value by a variable key in templates."""
    return d.get(key, '')
