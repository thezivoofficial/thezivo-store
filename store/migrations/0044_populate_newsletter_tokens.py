import secrets
from django.db import migrations


def populate_tokens(apps, schema_editor):
    NewsletterSubscriber = apps.get_model('store', 'NewsletterSubscriber')
    for sub in NewsletterSubscriber.objects.filter(token=''):
        sub.token = secrets.token_urlsafe(32)
        sub.save(update_fields=['token'])


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0043_newsletter_unsubscribe_token'),
    ]

    operations = [
        migrations.RunPython(populate_tokens, migrations.RunPython.noop),
    ]
