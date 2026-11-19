import uuid

from django.db import models


class Venta(models.Model):
    fecha = models.DateTimeField(auto_now_add=True)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    latitud = models.CharField(max_length=50, blank=True, null=True)
    longitud = models.CharField(max_length=50, blank=True, null=True)
    metodo_entrega = models.CharField(max_length=50, blank=True, null=True)
    direccion = models.TextField(blank=True, null=True)
    pagada = models.BooleanField(default=False)
    # El pedido ya está finalizado (detalle creado, stock descontado, email
    # enviado). Distinto de `pagada`: en transferencia/efectivo el pedido se
    # confirma antes de cobrar, y el pago se marca después desde el admin.
    confirmada = models.BooleanField(default=False)
    mp_payment_id = models.CharField(max_length=50, blank=True, null=True)
    items_json = models.JSONField(blank=True, null=True)
    metodo_pago = models.CharField(max_length=50, default='mercadopago')
    # Sesión del carrito que originó la venta. Se usa para vaciar el carrito
    # exactamente cuando el pedido se confirma (y evitar pedidos duplicados).
    session_key = models.CharField(max_length=100, blank=True, null=True)
    # SEGURIDAD: token impredecible para acceder a comprobantes y páginas
    # post-pago sin exponerse a enumeración de IDs (IDOR).
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    def __str__(self):
        return f"Venta {self.id}"
