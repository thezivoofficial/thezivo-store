from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0049_wishlistitem_color'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='color',
            field=models.CharField(blank=True, default='', help_text='Tag this image to a color variant (e.g. Red). Leave blank for shared images.', max_length=100),
        ),
    ]
