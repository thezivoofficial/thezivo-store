from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0048_returnrequest_bank_account_name_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='wishlistitem',
            name='color',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AlterUniqueTogether(
            name='wishlistitem',
            unique_together={('customer', 'product', 'color')},
        ),
    ]
