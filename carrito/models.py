from decimal import Decimal

from django.db import models
from productos.models import Producto, VarianteProducto
from ventas.models import Venta  # CAMBIO: se importa la Venta real, no se redefine


class Carrito(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    variante = models.ForeignKey(
        VarianteProducto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    cantidad = models.IntegerField(default=1)
    session_key = models.CharField(max_length=100, null=True, blank=True)

    @property
    def subtotal(self):
        precio = self.producto.precio
        if self.variante:
            precio += self.variante.precio_extra
        return precio * self.cantidad


# CAMBIO: se eliminó la clase Venta que estaba duplicada acá


class DetalleVenta(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE)  # CAMBIO: ahora apunta a ventas.Venta explícitamente
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    variante = models.ForeignKey(
        VarianteProducto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    cantidad = models.IntegerField()
    subtotal = models.DecimalField(max_digits=10, decimal_places=2) 