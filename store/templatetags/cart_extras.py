from django import template

register = template.Library()

@register.filter
def multiply(a, b):
    return a * b

@register.filter
def subtract(value, arg):
    return value - arg
