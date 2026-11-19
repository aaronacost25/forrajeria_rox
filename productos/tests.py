"""
Tests de localización de precios.

Con LANGUAGE_CODE='es-ar' los precios visibles usan coma decimal
($19000,50 — estilo argentino), pero todo valor que consume JavaScript
debe ir SIN localizar (punto decimal), porque Number('19000,50') = NaN.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from productos.models import Categoria, Producto


class LocalizacionPreciosJsTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Alimentos')
        self.producto = Producto.objects.create(
            nombre='Alimento Localización',
            categoria=self.categoria,
            precio=Decimal('19000.50'),
            stock=5,
            descripcion='Para tests de formato numérico',
        )

    def test_lista_data_price_sin_localizar(self):
        respuesta = self.client.get(reverse('lista_productos'))
        html = respuesta.content.decode('utf-8')
        self.assertIn('data-price="19000.50"', html)
        self.assertNotIn('data-price="19000,50"', html)
        # El texto visible sí va localizado
        self.assertIn('19000,50', html)

    def test_detalle_valores_js_sin_localizar(self):
        respuesta = self.client.get(
            reverse('detalle_producto', args=[self.producto.id])
        )
        html = respuesta.content.decode('utf-8')
        self.assertIn("Number('19000.50')", html)
        self.assertIn('data-price="19000.50"', html)
        self.assertNotIn('data-price="19000,50"', html)

    def test_carrito_data_attrs_sin_localizar(self):
        self.client.get(reverse('agregar_carrito', args=[self.producto.id]))
        respuesta = self.client.get(reverse('ver_carrito'))
        html = respuesta.content.decode('utf-8')
        self.assertIn('data-precio="19000.50"', html)
        self.assertIn('data-total="19000.50"', html)
        self.assertNotIn('data-precio="19000,50"', html)
        self.assertNotIn('data-total="19000,50"', html)
