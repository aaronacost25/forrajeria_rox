"""
Tests del flujo de compra y de las protecciones de seguridad.

Cubren:
- Cálculo de envío (tabla única de tarifas)
- Aislamiento de sesiones en el carrito (anti-IDOR)
- Tokens de venta en páginas post-pago y comprobantes (anti-enumeración)
- Verificación de pago real de MercadoPago antes de confirmar
- Flujo completo de pedido manual (efectivo/transferencia)
- Precios con Decimal (sin errores de float)
- Firma del webhook de MercadoPago
"""
from decimal import Decimal
import hashlib
import hmac
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from carrito.models import Carrito, DetalleVenta
from carrito.views import _pago_coincide_venta, calcular_envio
from productos.models import Categoria, Producto
from ventas.models import Venta


class CalculoEnvioTests(TestCase):
    def test_retiro_en_local_sin_costo(self):
        self.assertEqual(calcular_envio('local', 'cualquier direccion'), Decimal('0'))

    def test_zonas_conocidas(self):
        esperados = {
            'Villa Carlos Paz centro': Decimal('5000'),
            'MAYU SUMAJ calle 5': Decimal('1000'),
            'San Antonio de Arredondo': Decimal('4000'),
            'Icho Cruz ruta 14': Decimal('4000'),
        }
        for direccion, tarifa in esperados.items():
            with self.subTest(direccion=direccion):
                self.assertEqual(calcular_envio('envio', direccion), tarifa)

    def test_direccion_fuera_de_zona(self):
        # ENVIO_FUERA_DE_ZONA es 0 por decisión de negocio (documentado en views)
        self.assertEqual(calcular_envio('envio', 'Córdoba Capital'), Decimal('0'))

    def test_direccion_vacia(self):
        self.assertEqual(calcular_envio('envio', ''), Decimal('0'))
        self.assertEqual(calcular_envio('envio', None), Decimal('0'))


class BaseCarritoTests(TestCase):
    """Setup común: categoría, producto con stock y cliente logueado en su sesión."""

    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Alimentos')
        self.producto = Producto.objects.create(
            nombre='Alimento Test 20kg',
            categoria=self.categoria,
            precio=Decimal('33.33'),
            stock=5,
            descripcion='Producto de prueba',
        )


class CarritoAislamientoSesionTests(BaseCarritoTests):
    def test_no_se_puede_sumar_item_de_otra_sesion(self):
        item_ajeno = Carrito.objects.create(
            producto=self.producto, cantidad=1, session_key='sesion-ajena'
        )
        respuesta = self.client.post(f'/carrito/sumar/{item_ajeno.id}/')
        self.assertEqual(respuesta.status_code, 404)
        item_ajeno.refresh_from_db()
        self.assertEqual(item_ajeno.cantidad, 1)

    def test_no_se_puede_eliminar_item_de_otra_sesion(self):
        item_ajeno = Carrito.objects.create(
            producto=self.producto, cantidad=1, session_key='sesion-ajena'
        )
        respuesta = self.client.post(f'/carrito/eliminar/{item_ajeno.id}/')
        self.assertEqual(respuesta.status_code, 404)
        self.assertTrue(Carrito.objects.filter(id=item_ajeno.id).exists())

    def test_item_propio_si_se_puede_sumar(self):
        self.client.get(f'/carrito/agregar/{self.producto.id}/')  # crea la sesión
        session_key = self.client.session.session_key
        item = Carrito.objects.get(session_key=session_key)
        respuesta = self.client.post(f'/carrito/sumar/{item.id}/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['cantidad'], 2)

    def test_agregar_respeta_stock(self):
        self.producto.stock = 1
        self.producto.save()
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        session_key = self.client.session.session_key
        item = Carrito.objects.get(session_key=session_key)
        self.assertEqual(item.cantidad, 1)  # no puede superar el stock


class TokenVentaTests(BaseCarritoTests):
    def setUp(self):
        super().setUp()
        self.venta = Venta.objects.create(
            total=Decimal('1000'),
            metodo_pago='mercadopago',
            items_json=[{
                'producto_id': self.producto.id,
                'variante_id': None,
                'cantidad': 1,
                'subtotal': '33.33',
                'nombre': self.producto.nombre,
            }],
        )

    def test_pago_exitoso_sin_token_404(self):
        respuesta = self.client.get(f'/carrito/pago-exitoso/?venta_id={self.venta.id}')
        self.assertEqual(respuesta.status_code, 404)

    def test_pago_exitoso_con_token_falso_404(self):
        respuesta = self.client.get(
            f'/carrito/pago-exitoso/?venta_id={self.venta.id}'
            '&token=11111111-1111-1111-1111-111111111111'
        )
        self.assertEqual(respuesta.status_code, 404)

    def test_venta_mp_sin_pago_real_no_se_confirma(self):
        """Aun con token válido, si MP no tiene un pago aprobado la venta
        no se marca pagada ni descuenta stock."""
        with patch('carrito.views._pago_mp_aprobado', return_value=False):
            respuesta = self.client.get(
                f'/carrito/pago-exitoso/?venta_id={self.venta.id}&token={self.venta.token}'
            )
        self.assertEqual(respuesta.status_code, 200)
        self.venta.refresh_from_db()
        self.assertFalse(self.venta.pagada)
        self.assertEqual(DetalleVenta.objects.filter(venta=self.venta).count(), 0)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 5)

    def test_venta_mp_con_pago_aprobado_se_confirma(self):
        with patch('carrito.views._pago_mp_aprobado', return_value=True):
            respuesta = self.client.get(
                f'/carrito/pago-exitoso/?venta_id={self.venta.id}&token={self.venta.token}'
            )
        self.assertEqual(respuesta.status_code, 200)
        self.venta.refresh_from_db()
        self.assertTrue(self.venta.pagada)
        self.assertEqual(DetalleVenta.objects.filter(venta=self.venta).count(), 1)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 4)

    def test_confirmacion_idempotente(self):
        """Si el webhook ya confirmó, visitar pago-exitoso no duplica detalles."""
        with patch('carrito.views._pago_mp_aprobado', return_value=True):
            self.client.get(
                f'/carrito/pago-exitoso/?venta_id={self.venta.id}&token={self.venta.token}'
            )
            self.client.get(
                f'/carrito/pago-exitoso/?venta_id={self.venta.id}&token={self.venta.token}'
            )
        self.assertEqual(DetalleVenta.objects.filter(venta=self.venta).count(), 1)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 4)

    def test_venta_mp_pendiente_conserva_carrito_y_muestra_estado(self):
        """Si MP todavía no aprobó el pago, no se vacía el carrito ni se
        muestra “pago aprobado”."""
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        session_key = self.client.session.session_key
        self.venta.session_key = session_key
        self.venta.save(update_fields=['session_key'])

        with patch('carrito.views._pago_mp_aprobado', return_value=False):
            respuesta = self.client.get(
                f'/carrito/pago-exitoso/?venta_id={self.venta.id}'
                f'&token={self.venta.token}'
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Estamos esperando la confirmación del pago')
        self.assertContains(respuesta, 'Volver al carrito')
        self.assertTrue(Carrito.objects.filter(session_key=session_key).exists())

        self.venta.refresh_from_db()
        self.assertFalse(self.venta.confirmada)
        self.assertFalse(self.venta.pagada)

    def test_comprobantes_exigen_token(self):
        url_html = f'/carrito/comprobante/{self.venta.id}/{self.venta.token}/'
        url_pdf = f'/carrito/comprobante-pdf/{self.venta.id}/{self.venta.token}/'
        token_falso = '11111111-1111-1111-1111-111111111111'

        self.assertEqual(self.client.get(url_html).status_code, 200)
        respuesta_pdf = self.client.get(url_pdf)
        self.assertEqual(respuesta_pdf.status_code, 200)
        self.assertEqual(respuesta_pdf['Content-Type'], 'application/pdf')

        self.assertEqual(
            self.client.get(f'/carrito/comprobante-pdf/{self.venta.id}/{token_falso}/').status_code, 404
        )
        # Ruta vieja sin token ya no existe
        self.assertEqual(self.client.get(f'/carrito/comprobante/{self.venta.id}/').status_code, 404)


class FlujoPedidoManualTests(BaseCarritoTests):
    def test_pedido_efectivo_con_envio_confirma_y_descuenta_stock(self):
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        self.client.get(f'/carrito/agregar/{self.producto.id}/')

        respuesta = self.client.post(
            '/carrito/confirmar-pedido/',
            {
                'metodo_pago': 'efectivo',
                'metodo_entrega': 'envio',
                'direccion': 'Mayu Sumaj',
            },
            follow=True,
        )
        self.assertEqual(respuesta.status_code, 200)

        venta = Venta.objects.order_by('-id').first()
        # Los pedidos por transferencia/efectivo quedan CONFIRMADOS pero
        # pendientes de pago: los puntos se acreditan recién cuando el
        # vendedor marca la venta como pagada desde el admin.
        self.assertTrue(venta.confirmada)
        self.assertFalse(venta.pagada)
        self.assertEqual(venta.metodo_pago, 'efectivo')
        # 2 x 33.33 + 1000 (Mayu Sumaj) = 1066.66 exacto gracias a Decimal
        self.assertEqual(venta.total, Decimal('1066.66'))

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 3)

        # El carrito de la sesión queda vacío tras confirmar el pedido.
        session_key = self.client.session.session_key
        self.assertEqual(Carrito.objects.filter(session_key=session_key).count(), 0)

    def test_confirmar_pedido_por_get_no_permite(self):
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        respuesta = self.client.get('/carrito/confirmar-pedido/?metodo_pago=efectivo')
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(Venta.objects.count(), 0)

    def test_confirmar_pedido_exige_csrf(self):
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        cliente_sin_csrf = self.client_class(enforce_csrf_checks=True)
        cliente_sin_csrf.cookies = self.client.cookies
        respuesta = cliente_sin_csrf.post(
            '/carrito/confirmar-pedido/',
            {'metodo_pago': 'efectivo', 'metodo_entrega': 'local'},
        )
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(Venta.objects.count(), 0)

    def test_doble_clic_no_genera_dos_ventas(self):
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        datos = {'metodo_pago': 'efectivo', 'metodo_entrega': 'local'}
        self.client.post('/carrito/confirmar-pedido/', datos)
        self.client.post('/carrito/confirmar-pedido/', datos)
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(DetalleVenta.objects.count(), 1)

    def test_pedido_sin_carrito_redirige(self):
        respuesta = self.client.post('/carrito/confirmar-pedido/', {
            'metodo_pago': 'efectivo',
        })
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(Venta.objects.count(), 0)

    def test_vaciar_carrito_por_get_no_permite(self):
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        respuesta = self.client.get('/carrito/vaciar/')
        self.assertEqual(respuesta.status_code, 405)
        session_key = self.client.session.session_key
        self.assertEqual(Carrito.objects.filter(session_key=session_key).count(), 1)

    def test_pagar_por_get_no_permite(self):
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        respuesta = self.client.get('/carrito/pagar/')
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(Venta.objects.count(), 0)



class DecimalCuotasTests(BaseCarritoTests):
    def test_totales_de_cuotas_exactos(self):
        self.client.get(f'/carrito/agregar/{self.producto.id}/')
        self.client.get(f'/carrito/agregar/{self.producto.id}/')

        respuesta = self.client.get(
            '/carrito/elegir-cuotas/?metodo_entrega=envio&direccion=Villa Carlos Paz'
        )
        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode('utf-8')

        # base = 66.66 + 5000 = 5066.66 ; débito = 5066.66 / (1 - 0.0799) = 5506.64
        # Con LANGUAGE_CODE='es-ar' Django localiza el punto decimal como coma
        self.assertIn('5066,66', html)
        self.assertIn('5506,64', html)


class _FakePayment:
    def __init__(self, payload):
        self.payload = payload

    def get(self, payment_id):
        return {'status': 200, 'response': self.payload}


class _FakeSDK:
    def __init__(self, payload):
        self.payload = payload

    def payment(self):
        return _FakePayment(self.payload)


class WebhookFirmaTests(BaseCarritoTests):
    def setUp(self):
        super().setUp()
        self.venta = Venta.objects.create(
            total=Decimal('1000'),
            metodo_pago='mercadopago',
            items_json=[{
                'producto_id': self.producto.id,
                'variante_id': None,
                'cantidad': 1,
                'subtotal': '1000',
                'nombre': self.producto.nombre,
            }],
        )
        self.pago = {
            'id': 123,
            'status': 'approved',
            'external_reference': str(self.venta.id),
            'currency_id': 'ARS',
            'transaction_amount': 1000,
        }

    def _firma(self, secret='secreto-webhook', data_id='123',
               request_id='req-1', ts='123'):
        manifest = f'id:{data_id};request-id:{request_id};ts:{ts};'
        firma = hmac.new(
            secret.encode(), manifest.encode(), hashlib.sha256
        ).hexdigest()
        return f'ts={ts},v1={firma}'

    def _post(self, pago=None, firma=None):
        pago = pago or self.pago
        firma = firma or self._firma()
        with patch(
            'carrito.views.mercadopago.SDK',
            return_value=_FakeSDK(pago),
        ):
            return self.client.post(
                '/carrito/webhook/?data.id=123',
                data='{"type": "payment", "data": {"id": "123"}}',
                content_type='application/json',
                HTTP_X_SIGNATURE=firma,
                HTTP_X_REQUEST_ID='req-1',
            )

    def test_webhook_sin_firma_403(self):
        respuesta = self.client.post(
            '/carrito/webhook/',
            data='{"type": "payment", "data": {"id": "1"}}',
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_webhook_con_firma_invalida_403(self):
        respuesta = self.client.post(
            '/carrito/webhook/?data.id=1',
            data='{"type": "payment", "data": {"id": "1"}}',
            content_type='application/json',
            HTTP_X_SIGNATURE='ts=123,v1=falso',
            HTTP_X_REQUEST_ID='req-1',
        )
        self.assertEqual(respuesta.status_code, 403)

    @override_settings(MP_WEBHOOK_SECRET='secreto-webhook', MP_ACCESS_TOKEN='token-test')
    def test_webhook_con_firma_valida_y_pago_aprobado_confirma(self):
        respuesta = self._post()
        self.assertEqual(respuesta.status_code, 200)

        self.venta.refresh_from_db()
        self.assertTrue(self.venta.confirmada)
        self.assertTrue(self.venta.pagada)
        self.assertEqual(self.venta.mp_payment_id, '123')
        self.assertEqual(DetalleVenta.objects.filter(venta=self.venta).count(), 1)

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 4)

    @override_settings(MP_WEBHOOK_SECRET='secreto-webhook', MP_ACCESS_TOKEN='token-test')
    def test_webhook_rechaza_monto_menor_al_total(self):
        respuesta = self._post(pago={**self.pago, 'transaction_amount': 1})
        self.assertEqual(respuesta.status_code, 400)

        self.venta.refresh_from_db()
        self.assertFalse(self.venta.confirmada)
        self.assertFalse(self.venta.pagada)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 5)

    @override_settings(MP_WEBHOOK_SECRET='secreto-webhook', MP_ACCESS_TOKEN='token-test')
    def test_webhook_rechaza_venta_que_no_es_de_mercado_pago(self):
        self.venta.metodo_pago = 'efectivo'
        self.venta.save(update_fields=['metodo_pago'])
        respuesta = self._post()
        self.assertEqual(respuesta.status_code, 400)

        self.venta.refresh_from_db()
        self.assertFalse(self.venta.confirmada)

    @override_settings(MP_WEBHOOK_SECRET='secreto-webhook', MP_ACCESS_TOKEN='token-test')
    def test_webhook_pago_pendiente_no_confirma(self):
        respuesta = self._post(pago={**self.pago, 'status': 'pending'})
        self.assertEqual(respuesta.status_code, 200)

        self.venta.refresh_from_db()
        self.assertFalse(self.venta.confirmada)
        self.assertFalse(self.venta.pagada)

    @override_settings(MP_WEBHOOK_SECRET='')
    def test_webhook_rechaza_firma_si_el_secreto_esta_vacio(self):
        respuesta = self._post(firma=self._firma(secret=''))
        self.assertEqual(respuesta.status_code, 403)


class PagoCoincideVentaTests(BaseCarritoTests):
    """El pago aprobado debe ser de la venta, en pesos y por el total."""

    def setUp(self):
        super().setUp()
        self.venta = Venta.objects.create(
            total=Decimal('1000'),
            metodo_pago='mercadopago',
        )
        self.pago = {
            'status': 'approved',
            'external_reference': str(self.venta.id),
            'currency_id': 'ARS',
            'transaction_amount': 1000,
        }

    def test_pago_correcto(self):
        self.assertTrue(_pago_coincide_venta(self.venta, self.pago))

    def test_rechaza_monto_menor(self):
        self.assertFalse(
            _pago_coincide_venta(
                self.venta, {**self.pago, 'transaction_amount': 999.99}
            )
        )

    def test_rechaza_moneda_distinta(self):
        self.assertFalse(
            _pago_coincide_venta(
                self.venta, {**self.pago, 'currency_id': 'USD'}
            )
        )

    def test_rechaza_otra_venta(self):
        self.assertFalse(
            _pago_coincide_venta(
                self.venta, {**self.pago, 'external_reference': '999'}
            )
        )

    def test_rechaza_pago_no_aprobado(self):
        self.assertFalse(
            _pago_coincide_venta(
                self.venta, {**self.pago, 'status': 'pending'}
            )
        )


class PuertaTraseraAdminTests(TestCase):
    def test_ruta_crear_admin_eliminada(self):
        self.assertEqual(self.client.get('/productos/crear-admin/').status_code, 404)
        self.assertEqual(self.client.get('/crear-admin/').status_code, 404)
