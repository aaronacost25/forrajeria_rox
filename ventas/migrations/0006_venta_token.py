import uuid

from django.db import migrations, models


def generar_tokens(apps, schema_editor):
    """Asigna un token único a cada venta existente."""
    Venta = apps.get_model('ventas', 'Venta')
    for venta in Venta.objects.all():
        venta.token = uuid.uuid4()
        venta.save(update_fields=['token'])


def no_hacer_nada(apps, schema_editor):
    """Al revertir, los tokens se descartan junto con el campo."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ventas', '0005_venta_metodo_pago'),
    ]

    operations = [
        # 1) Agregar el campo permitiendo NULL y sin restricción unique
        migrations.AddField(
            model_name='venta',
            name='token',
            field=models.UUIDField(null=True, editable=False),
        ),
        # 2) Generar un token distinto para cada venta ya existente
        migrations.RunPython(generar_tokens, no_hacer_nada),
        # 3) Volverlo obligatorio y único (con default para ventas nuevas)
        migrations.AlterField(
            model_name='venta',
            name='token',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
