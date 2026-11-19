from django.shortcuts import render, redirect, get_object_or_404
from .models import Carrito, DetalleVenta
from ventas.models import Venta
from productos.models import Producto, VarianteProducto
import mercadopago
from django.http import HttpResponse, JsonResponse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from reportlab.lib.utils import ImageReader
import os
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from forrajeria_web.emails import enviar_email
from django.urls import reverse
from django.utils import timezone
from django.contrib.staticfiles import finders
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from urllib.parse import quote
import hmac
import hashlib

COMISIONES = {
    'debito': Decimal('0.0799'),
    '1':      Decimal('0.0799'),
    '3':      Decimal('0.2274'),
    '6':      Decimal('0.3108'),
    '9':      Decimal('0.4101'),
}

# Tarifas de envío por localidad (única fuente de verdad — antes estaban
# duplicadas en ver_carrito, elegir_cuotas, pagar y confirmar_pedido_manual).
TARIFAS_ENVIO = [
    ('villa carlos paz', Decimal('5000')),
    ('mayu sumaj',       Decimal('1000')),
    ('san antonio',      Decimal('4000')),
    ('icho cruz',        Decimal('4000')),
]

# Nombres que se muestran en el carrito para cada zona. Se derivan de la
# misma tabla de arriba para no tener dos listas de precios (el JS recibe
# este diccionario ya calculado).
LOCALIDADES_ENVIO = {
    'villa carlos paz': 'Villa Carlos Paz',
    'mayu sumaj': 'Mayu Sumaj',
    'san antonio': 'San Antonio de Arredondo',
    'icho cruz': 'Icho Cruz',
}

TARIFAS_ENVIO_UI = {
    LOCALIDADES_ENVIO[localidad]: int(tarifa)
    for localidad, tarifa in TARIFAS_ENVIO
}

# Qué cobrar cuando el método es envío pero la dirección no coincide con
# ninguna zona conocida. Está en 0 (envío gratis) para no cambiar el
# comportamiento actual; si preferís un monto fijo, cambialo acá.
ENVIO_FUERA_DE_ZONA = Decimal('0')


def calcular_envio(metodo_entrega, direccion):
    """Devuelve el costo de envío (Decimal) según método y dirección."""
    if metodo_entrega != 'envio':
        return Decimal('0')
    direccion_lower = (direccion or '').lower()
    for localidad, tarifa in TARIFAS_ENVIO:
        if localidad in direccion_lower:
            return tarifa
    return ENVIO_FUERA_DE_ZONA


def _money(value):
    """Redondea a 2 decimales como se redondea el dinero (medio, arriba)."""
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def elegir_cuotas(request):
    session_key = request.session.session_key
    items = Carrito.objects.filter(session_key=session_key)

    metodo_entrega = request.GET.get('metodo_entrega', 'local')
    direccion = request.GET.get('direccion', '')

    total_productos = sum((item.subtotal for item in items), Decimal('0'))
    envio = calcular_envio(metodo_entrega, direccion)

    total_base = _money(total_productos + envio)

    definiciones = [
        ('debito', 'Débito',           '7,99%'),
        ('1',      'Crédito 1 pago',   '7,99%'),
        ('3',      'Crédito 3 cuotas', '22,74%'),
        ('6',      'Crédito 6 cuotas', '31,08%'),
        ('9',      'Crédito 9 cuotas', '41,01%'),
    ]

    opciones = []
    for valor, label, comision_pct in definiciones:
        total = _money(total_base / (Decimal('1') - COMISIONES[valor]))
        op = {'valor': valor, 'label': label, 'comision_pct': comision_pct, 'total': total}
        if valor in ('3', '6', '9'):
            op['cuota'] = _money(total / int(valor))
        opciones.append(op)

    return render(request, 'carrito/elegir_cuotas.html', {
        'opciones': opciones,
        'total_base': total_base,
        'metodo_entrega': metodo_entrega,
        'direccion': direccion,
        })


@require_POST
def pagar(request):
    if not settings.MP_ACCESS_TOKEN:
        messages.error(
            request,
            'El pago con Mercado Pago no está disponible en este momento. '
            'Elegí transferencia o efectivo.',
        )
        return redirect('ver_carrito')

    sdk = mercadopago.SDK(settings.MP_ACCESS_TOKEN)

    session_key = request.session.session_key
    items_carrito = Carrito.objects.filter(session_key=session_key)

    if not items_carrito.exists():
        messages.warning(request, 'Tu carrito está vacío.')
        return redirect('ver_carrito')

    metodo_entrega = (
        'envio' if request.POST.get('metodo_entrega') == 'envio' else 'local'
    )
    direccion = request.POST.get('direccion', '').strip()[:200]
    cuotas = request.POST.get('cuotas', '1')
    if cuotas not in COMISIONES:
        cuotas = '1'

    comision = COMISIONES[cuotas]

    items = []
    items_snapshot = []
    total_productos = Decimal('0')

    for item in items_carrito:
        titulo = item.producto.nombre
        if item.variante:
            opciones = " - ".join(o.nombre for o in item.variante.opciones.all())
            titulo = f"{titulo} ({opciones})"

        precio_base = item.producto.precio
        if item.variante:
            precio_base += item.variante.precio_extra

        items.append({
            "title": titulo,
            "quantity": item.cantidad,
            "unit_price": float(_money(precio_base)),
        })

        total_productos += item.cantidad * precio_base

        items_snapshot.append({
            'producto_id': item.producto.id,
            'variante_id': item.variante.id if item.variante else None,
            'cantidad': item.cantidad,
            'subtotal': str(item.subtotal),
            'nombre': titulo,
        })

    total_envio = calcular_envio(metodo_entrega, direccion)
    if total_envio > 0:
        items.append({
            "title": "Costo de envío",
            "quantity": 1,
            "unit_price": float(total_envio),
        })

    total_base = _money(total_productos + total_envio)

    total_final = _money(total_base / (Decimal('1') - comision))
    recargo = _money(total_final - total_base)

    if recargo > 0:
        items.append({
            "title": "Cargo por procesamiento de pago",
            "quantity": 1,
            "unit_price": float(recargo),
        })

    venta = Venta.objects.create(
        total=total_final,
        metodo_entrega=metodo_entrega,
        direccion=direccion,
        items_json=items_snapshot,
        metodo_pago='mercadopago',
        session_key=session_key,
    )

    back_url_success = (
        f"{request.build_absolute_uri(reverse('pago_exitoso'))}"
        f"?venta_id={venta.id}"
        f"&token={venta.token}"
        f"&metodo_entrega={quote(metodo_entrega)}"
        f"&direccion={quote(direccion)}"
    )
    carrito_url = request.build_absolute_uri(reverse('ver_carrito'))

    if cuotas == 'debito':
        payment_methods = {
            "excluded_payment_types": [{"id": "credit_card"}],
            "installments": 1,
            "default_installments": 1,
        }
    else:
        n = int(cuotas)
        payment_methods = {
            "excluded_payment_types": [{"id": "debit_card"}],
            "installments": n,
            "default_installments": n,
        }

    preference_data = {
        "items": items,
        "external_reference": str(venta.id),
        # Mercado Pago avisa acá cuando el pago cambia de estado. Sin esto,
        # el webhook solo funciona si se cargó la URL a mano en el panel.
        "notification_url": request.build_absolute_uri(reverse('webhook')),
        "payment_methods": payment_methods,
        "back_urls": {
            "success": back_url_success,
            "failure": carrito_url,
            "pending": carrito_url,
        },
        "auto_return": "approved",
    }

    preference_response = sdk.preference().create(preference_data)

    if "response" in preference_response:
        preference = preference_response["response"]
        if "init_point" in preference:
            return redirect(preference["init_point"])

    print(f"Error creando preferencia de Mercado Pago: {preference_response}")
    messages.error(
        request,
        'No pudimos iniciar el pago con Mercado Pago. '
        'Probá de nuevo o elegí transferencia/efectivo.',
    )
    return redirect('ver_carrito')


def _confirmar_venta(venta, marcar_pagada=True):
    """
    Función central e idempotente — sin importar si la llama el webhook,
    pago_exitoso o confirmar_pedido_manual, la venta se completa UNA sola vez:
    crea los DetalleVenta, descuenta stock, vacía el carrito y manda el email.
    """
    detalles_creados = []  # para armar el email después

    with transaction.atomic():
        # Bloqueamos la venta: si el webhook y el retorno de MP llegan a la
        # vez, solo uno confirma, crea el detalle y descuenta el stock.
        venta_original = venta
        venta = Venta.objects.select_for_update().get(pk=venta.pk)

        # `pagada` cubre las ventas anteriores al campo `confirmada`, que ya
        # estaban finalizadas y pagadas (así un webhook que reintenta no
        # vuelve a crear el detalle ni a descontar stock).
        if venta.confirmada or venta.pagada:
            return

        for item_data in (venta.items_json or []):
            producto = (
                Producto.objects.select_for_update()
                .filter(pk=item_data.get('producto_id'))
                .first()
            )
            if not producto:
                # El producto se borró después de armar el carrito. No se
                # puede crear el DetalleVenta, pero el pedido se confirma y
                # queda el snapshot en items_json para el vendedor.
                print(
                    f'Venta {venta.id}: producto '
                    f'{item_data.get("producto_id")} ya no existe.'
                )
                detalles_creados.append(item_data)
                continue

            variante = None
            variante_id = item_data.get('variante_id')
            if variante_id:
                variante = (
                    VarianteProducto.objects.select_for_update()
                    .filter(pk=variante_id, producto=producto)
                    .first()
                )

            DetalleVenta.objects.create(
                venta=venta,
                producto=producto,
                variante=variante,
                cantidad=item_data['cantidad'],
                subtotal=item_data['subtotal'],
            )

            cantidad = int(item_data.get('cantidad') or 0)
            if cantidad > 0:
                if variante:
                    variante.stock -= cantidad
                    variante.save(update_fields=['stock'])
                else:
                    producto.stock -= cantidad
                    producto.save(update_fields=['stock'])

            detalles_creados.append(item_data)

        venta.confirmada = True
        if marcar_pagada:
            venta.pagada = True
        venta.save(update_fields=['confirmada', 'pagada'])

        # Vaciamos el carrito exactamente al confirmar, dentro de la misma
        # transacción. Así un doble clic no genera dos pedidos.
        if venta.session_key:
            Carrito.objects.filter(session_key=venta.session_key).delete()

        # El email se manda después del commit (no dentro de la transacción).
        transaction.on_commit(
            lambda: _enviar_email_notificacion(venta, detalles_creados)
        )

    # Mantenemos sincronizado el objeto que recibió el llamador.
    venta_original.confirmada = venta.confirmada
    venta_original.pagada = venta.pagada


def _enviar_email_notificacion(venta, items):
    """
    CAMBIO: función nueva — manda un email automático apenas se confirma
    una venta, sin depender de que el cliente haga nada.
    """
    lineas_productos = "\n".join(
        f"  - {item['nombre']} x{item['cantidad']} = ${item['subtotal']}"
        for item in items
    )

    entrega_texto = f"Método de entrega: {venta.metodo_entrega or 'No especificado'}"
    if venta.metodo_entrega == 'envio':
        entrega_texto += "\n⚠️ El cliente todavía tiene que mandarte la ubicación exacta por WhatsApp."

    cuerpo = f"""🐾 NUEVO PEDIDO CONFIRMADO — Forrajería Rox

Venta N° {venta.id}
Fecha: {timezone.localtime(venta.fecha).strftime('%d/%m/%Y %H:%M')}

Productos:
{lineas_productos}

Total: ${venta.total}
{entrega_texto}

ID de pago Mercado Pago: {venta.mp_payment_id or 'No disponible'}
"""

    try:
        enviar_email(
            f'🐾 Nuevo pedido N° {venta.id} — ${venta.total}',
            cuerpo,
            [settings.NOTIFICATION_EMAIL],
        )
    except Exception as e:
        print(f"Error enviando email de notificación: {e}")


@csrf_exempt
def webhook(request):
    try:
        data = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, TypeError):
        return HttpResponse(status=400)

    body_id = (data.get('data') or {}).get('id')
    query_id = request.GET.get('data.id')
    # La firma de MP se calcula con el data.id que viene en la query. Si no
    # viene, usamos el del body (y después exigimos que coincidan).
    data_id_firma = query_id or (str(body_id) if body_id else '')

    if not validar_firma_mp(request, data_id=data_id_firma):
        return HttpResponse(status=403)

    if query_id and body_id and str(query_id) != str(body_id):
        return HttpResponse(status=400)

    tipo = data.get('type') or data.get('topic')
    accion = data.get('action') or ''
    if tipo != 'payment' and not accion.startswith('payment.'):
        return HttpResponse("OK")

    payment_id = body_id or query_id or request.GET.get('id')
    if not payment_id:
        return HttpResponse(status=400)

    if not settings.MP_ACCESS_TOKEN:
        return HttpResponse(status=503)

    sdk = mercadopago.SDK(settings.MP_ACCESS_TOKEN)
    try:
        payment_response = sdk.payment().get(payment_id)
    except Exception as exc:
        print(f'Error consultando el pago {payment_id} en MP: {exc}')
        return HttpResponse(status=502)

    if payment_response.get("status") != 200:
        return HttpResponse(status=400)

    payment_info = payment_response["response"]

    if payment_info.get("status") != "approved":
        # Rechazado, pendiente, etc.: no confirmamos la venta.
        return HttpResponse("OK")

    venta_id = payment_info.get("external_reference")
    if not venta_id:
        return HttpResponse(status=400)

    try:
        venta = Venta.objects.get(id=venta_id)
    except (Venta.DoesNotExist, ValueError, TypeError):
        return HttpResponse(status=404)

    # SEGURIDAD: el pago tiene que ser de esta venta, en pesos y por el
    # monto correcto. Sin esto, un pago aprobado de $1 podría confirmar
    # una compra de $50.000.
    if not _pago_coincide_venta(venta, payment_info):
        print(
            f'Webhook MP rechazado: pago {payment_id} no coincide con la '
            f'venta {venta.id} (total ${venta.total}).'
        )
        return HttpResponse(status=400)

    venta.mp_payment_id = str(payment_id)
    venta.save(update_fields=["mp_payment_id"])
    _confirmar_venta(venta)

    return HttpResponse("OK")


def _pago_coincide_venta(venta, pago):
    """True si un pago de MP corresponde exactamente a esta venta."""
    if venta.metodo_pago != 'mercadopago':
        return False
    if str(pago.get('external_reference')) != str(venta.id):
        return False
    if pago.get('status') != 'approved':
        return False
    moneda = pago.get('currency_id')
    if moneda and moneda != 'ARS':
        return False
    try:
        monto = Decimal(str(pago.get('transaction_amount')))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return monto >= venta.total


def validar_firma_mp(request, data_id=None):
    x_signature = request.headers.get('x-signature', '')
    x_request_id = request.headers.get('x-request-id', '')

    if not x_signature or not x_request_id:
        return False

    partes = dict(p.split('=', 1) for p in x_signature.split(',') if '=' in p)
    ts = partes.get('ts')
    v1_recibido = partes.get('v1')

    if not ts or not v1_recibido:
        return False

    if data_id is None:
        data_id = request.GET.get('data.id', '')
    data_id = str(data_id or '')
    if not data_id:
        return False

    manifest = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"

    secret = settings.MP_WEBHOOK_SECRET
    if not secret:
        # Sin secreto configurado cualquiera podría firmar un webhook.
        return False
    firma_calculada = hmac.new(
        secret.encode(), manifest.encode(), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(firma_calculada, v1_recibido)


def agregar_carrito(request, producto_id):
    producto = get_object_or_404(Producto, id=producto_id)

    variante_id = request.GET.get('variante_id')
    variante = None
    if variante_id:
        variante = get_object_or_404(VarianteProducto, id=variante_id, producto=producto)

    if producto.variantes.exists() and not variante:
        messages.error(request, f'Elegí una presentación antes de agregar {producto.nombre}.')
        return redirect('detalle_producto', id=producto_id)

    session_key = request.session.session_key
    if not session_key:
        request.session.create()
        session_key = request.session.session_key

    stock_disponible = variante.stock if variante else producto.stock

    if stock_disponible <= 0:
        messages.error(request, f'❌ {producto.nombre} no tiene stock disponible.')
        return redirect('detalle_producto', id=producto_id)

    item = Carrito.objects.filter(
        producto=producto,
        variante=variante,
        session_key=session_key,
    ).first()

    if item is None:
        Carrito.objects.create(
            producto=producto,
            variante=variante,
            session_key=session_key,
            cantidad=1,
        )
    elif item.cantidad < stock_disponible:
        item.cantidad += 1
        item.save()
    else:
        messages.warning(request, f'⚠️ Solo hay {stock_disponible} unidades disponibles.')
        return redirect('detalle_producto', id=producto_id)

    return redirect('ver_carrito')


@never_cache
def ver_carrito(request):
    session_key = request.session.session_key
    items = list(Carrito.objects.filter(session_key=session_key))

    total_productos = sum((item.subtotal for item in items), Decimal('0'))

    solo_premios = False

    direccion = request.GET.get('direccion', '')
    metodo_entrega = request.GET.get('metodo_entrega', 'local')

    envio = calcular_envio(metodo_entrega, direccion)

    total = total_productos + envio

    return render(request, 'carrito/ver_carrito.html', {
        'items': items,
        'total': total,
        'total_productos': total_productos,
        'envio': envio,
        'direccion': direccion,
        'metodo_entrega': metodo_entrega,
        'solo_premios': solo_premios,
        # Tarifas para el JS (una sola fuente de verdad con calcular_envio).
        'tarifas_envio': TARIFAS_ENVIO_UI,
    })


@require_POST
def sumar_cantidad(request, item_id):
    # SEGURIDAD: el ítem debe pertenecer a la sesión actual
    item = get_object_or_404(Carrito, id=item_id, session_key=request.session.session_key)
    stock_disponible = item.variante.stock if item.variante else item.producto.stock

    if item.cantidad < stock_disponible:
        item.cantidad += 1
        item.save()

    session_key = request.session.session_key
    total = sum(i.subtotal for i in Carrito.objects.filter(session_key=session_key))
    return JsonResponse({'cantidad': item.cantidad, 'subtotal': item.subtotal, 'total': total})


@require_POST
def restar_cantidad(request, item_id):
    # SEGURIDAD: el ítem debe pertenecer a la sesión actual
    item = get_object_or_404(Carrito, id=item_id, session_key=request.session.session_key)

    if item.cantidad > 1:
        item.cantidad -= 1
        item.save()

    session_key = request.session.session_key
    total = sum(i.subtotal for i in Carrito.objects.filter(session_key=session_key))
    return JsonResponse({'cantidad': item.cantidad, 'subtotal': item.subtotal, 'total': total})


@require_POST
def eliminar_producto(request, item_id):
    # SEGURIDAD: el ítem debe pertenecer a la sesión actual
    item = get_object_or_404(Carrito, id=item_id, session_key=request.session.session_key)
    item.delete()

    session_key = request.session.session_key
    total = sum(i.subtotal for i in Carrito.objects.filter(session_key=session_key))
    return JsonResponse({'total': total})
@require_POST
def confirmar_pedido_manual(request):
    """Confirma un pedido con transferencia/efectivo.

    Es POST + CSRF a propósito: antes era un GET que creaba la venta y
    descontaba stock, así que un simple enlace (o el botón Atrás/recarga)
    podía duplicar pedidos.
    """
    session_key = request.session.session_key
    if not session_key:
        messages.warning(request, 'Tu carrito está vacío.')
        return redirect('ver_carrito')

    metodo_entrega = request.POST.get('metodo_entrega', 'local')
    if metodo_entrega not in ('local', 'envio'):
        metodo_entrega = 'local'

    direccion = request.POST.get('direccion', '').strip()[:200]

    metodo_pago = request.POST.get('metodo_pago') or 'efectivo'
    if metodo_pago not in ('efectivo', 'transferencia'):
        messages.error(request, 'Elegí un método de pago válido.')
        return redirect('ver_carrito')

    with transaction.atomic():
        # Bloqueamos las filas del carrito: si llegan dos POST por un doble
        # clic, el segundo espera y después ya no encuentra ítems.
        items_carrito = list(
            Carrito.objects.select_for_update()
            .filter(session_key=session_key)
            .select_related('producto', 'variante')
            .prefetch_related('variante__opciones')
        )
        if not items_carrito:
            return redirect('ver_carrito')

        # Validamos stock antes de confirmar: si alguien compró antes o el
        # stock cambió, avisamos en lugar de dejar la venta inconsistente.
        for item in items_carrito:
            stock_disponible = (
                item.variante.stock if item.variante else item.producto.stock
            ) or 0
            if item.cantidad > stock_disponible:
                nombre = item.producto.nombre
                if item.variante:
                    opciones = " - ".join(
                        o.nombre for o in item.variante.opciones.all()
                    )
                    if opciones:
                        nombre = f"{nombre} ({opciones})"
                messages.error(
                    request,
                    f'No hay stock suficiente de {nombre}. '
                    f'Disponible: {stock_disponible}.',
                )
                return redirect('ver_carrito')

        items_snapshot = []
        total_productos = Decimal('0')

        for item in items_carrito:
            titulo = item.producto.nombre
            if item.variante:
                opciones = " - ".join(o.nombre for o in item.variante.opciones.all())
                titulo = f"{titulo} ({opciones})"

            precio_base = item.producto.precio
            if item.variante:
                precio_base += item.variante.precio_extra

            total_productos += item.cantidad * precio_base

            items_snapshot.append({
                'producto_id': item.producto.id,
                'variante_id': item.variante.id if item.variante else None,
                'cantidad': item.cantidad,
                'subtotal': str(item.subtotal),
                'nombre': titulo,
            })

        total_envio = calcular_envio(metodo_entrega, direccion)

        total_final = _money(total_productos + total_envio)

        venta = Venta.objects.create(
            total=total_final,
            metodo_entrega=metodo_entrega,
            direccion=direccion,
            metodo_pago=metodo_pago,
            items_json=items_snapshot,
            session_key=session_key,
        )

        # Pedido manual (transferencia/efectivo): se confirma pero queda
        # PENDIENTE DE PAGO.
        _confirmar_venta(venta, marcar_pagada=False)

    url = reverse('pago_exitoso')
    return redirect(
        f"{url}?venta_id={venta.id}&token={venta.token}"
        f"&metodo_entrega={quote(metodo_entrega)}&direccion={quote(direccion)}"
    )


def _pago_mp_aprobado(venta):
    """
    SEGURIDAD: consulta a MercadoPago si existe un pago aprobado real
    para esta venta (por external_reference). Si lo encuentra, guarda el
    payment id y devuelve True. Así nadie puede confirmar una venta
    simplemente fabricando la URL /carrito/pago-exitoso/?venta_id=X.
    """
    if not settings.MP_ACCESS_TOKEN:
        return False
    try:
        sdk = mercadopago.SDK(settings.MP_ACCESS_TOKEN)
        response = sdk.payment().search({"external_reference": str(venta.id)})
    except Exception as e:
        print(f"Error consultando pagos en MP: {e}")
        return False

    if response.get("status") != 200:
        return False

    results = (response.get("response") or {}).get("results") or []
    # No alcanza con que haya un pago aprobado: tiene que ser de ESTA venta,
    # en pesos y por el monto total.
    aprobados = [p for p in results if _pago_coincide_venta(venta, p)]
    if not aprobados:
        return False

    venta.mp_payment_id = str(aprobados[0].get("id"))
    venta.save(update_fields=["mp_payment_id"])
    return True


@never_cache
def pago_exitoso(request):
    session_key = request.session.session_key
    venta_id = request.GET.get('venta_id')
    token = request.GET.get('token')

    if not venta_id:
        return HttpResponse("Falta venta_id", status=400)

    # SEGURIDAD: se exige el token de la venta; sin él, 404. Así nadie
    # puede ver datos de una venta ajena probando IDs.
    venta = get_object_or_404(Venta, id=venta_id, token=token)

    # Las ventas de MercadoPago solo se confirman si hay un pago aprobado
    # verificado contra la API (o si ya las confirmó el webhook firmado).
    # Las ventas manuales (efectivo/transferencia) ya vienen confirmadas
    # por confirmar_pedido_manual.
    if venta.metodo_pago == 'mercadopago' and not venta.pagada:
        if _pago_mp_aprobado(venta):
            _confirmar_venta(venta)
            venta.refresh_from_db(fields=['confirmada', 'pagada'])

    # Datos de entrega: salen de la venta, no de la URL (la URL se puede
    # manipular y antes mostraba una dirección distinta a la guardada).
    metodo_entrega = venta.metodo_entrega or 'local'
    metodo_entrega_label = (
        'Envío a domicilio' if metodo_entrega == 'envio' else 'Retiro en local'
    )
    direccion = venta.direccion or 'No especificada'

    items_json = venta.items_json or []
    solo_premios = False
    confirmada = venta.confirmada or venta.pagada
    es_premio_sin_pago = False
    pago_pendiente = venta.metodo_pago == 'mercadopago' and not venta.pagada
    ir_a_whatsapp = (
        (metodo_entrega == 'envio' and confirmada)
    )

    detalles = venta.detalleventa_set.all()

    # URL absoluta del comprobante en PDF (protegida con token)
    comprobante_url = request.build_absolute_uri(
        reverse('generar_comprobante', args=[venta.id, venta.token])
    )

    mensaje = "🐾 *Forrajería Rox*\n"
    mensaje += "━━━━━━━━━━━━━━━━━━━━\n"
    mensaje += "✅ *NUEVO PEDIDO RECIBIDO*\n"
    mensaje += "━━━━━━━━━━━━━━━━━━━━\n\n"
    mensaje += "🛒 *Productos:*\n"

    items_resumen = []
    if items_json:
        # Usamos el snapshot: funciona incluso si el pago todavía está
        # pendiente y sirve para que el mensaje coincida con lo comprado.
        for item in items_json:
            nombre_item = item.get('nombre') or 'Producto'
            cantidad = item.get('cantidad') or 0
            subtotal = item.get('subtotal') or '0'

            items_resumen.append({
                'nombre': nombre_item,
                'cantidad': cantidad,
                'subtotal': subtotal,
            })
            mensaje += f"▪ {nombre_item}\n"
            mensaje += f"  Cantidad: {cantidad} = ${subtotal}\n"
    else:
        # Compatibilidad con ventas viejas que no tienen items_json.
        for d in detalles:
            nombre_item = d.producto.nombre
            if d.variante:
                opciones = " - ".join(o.nombre for o in d.variante.opciones.all())
                nombre_item = f"{nombre_item} ({opciones})"

            items_resumen.append({
                'nombre': nombre_item,
                'cantidad': d.cantidad,
                'subtotal': d.subtotal,
            })
            mensaje += f"▪ {nombre_item}\n"
            mensaje += f"  Cantidad: {d.cantidad} = ${d.subtotal}\n"

    # El carrito se vacía recién cuando el pedido está confirmado. Si el
    # pago de MP sigue pendiente, lo conservamos para que el cliente pueda
    # reintentar o corregir el pedido.
    if confirmada:
        Carrito.objects.filter(session_key=session_key).delete()

    mensaje += "\n━━━━━━━━━━━━━━━━━━━━\n"
    mensaje += f"💰 *Total:* ${venta.total}\n"
    metodo_pago_label = {
        'mercadopago': 'Mercado Pago',
        'transferencia': 'Transferencia bancaria',
        'efectivo': 'Efectivo',
    }.get(venta.metodo_pago, venta.metodo_pago)
    mensaje += f"💳 *Pago:* {metodo_pago_label}\n"
    mensaje += f"🚚 *Entrega:* {metodo_entrega_label}\n"

    if metodo_entrega == 'envio':
        mensaje += f"📍 *Dirección:* {direccion}\n"
        mensaje += "\n📍 *Te paso la ubicación exacta por este chat.*"

    mensaje += "\n━━━━━━━━━━━━━━━━━━━━\n"
    mensaje += f"🔢 *Nº de venta:* {venta.id}\n"
    mensaje += (
        f"📅 *Fecha:* "
        f"{timezone.localtime(venta.fecha).strftime('%d/%m/%Y %H:%M')}\n"
    )
    mensaje += f"📄 *Comprobante:* {comprobante_url}"

    return render(request, 'carrito/pago_exitoso.html', {
        'venta': venta,
        'items': items_resumen,
        'total': venta.total,
        'metodo_entrega': metodo_entrega,
        'metodo_entrega_label': metodo_entrega_label,
        'direccion': direccion,
        'solo_premios': solo_premios,
        'es_premio_sin_pago': es_premio_sin_pago,
        'pago_pendiente': pago_pendiente,
        'puede_comprobante': confirmada,
        'ir_a_whatsapp': ir_a_whatsapp,
        'whatsapp_url': f"https://wa.me/5493541343314?text={quote(mensaje)}",
    })


def pago_error(request):
    return render(request, 'carrito/pago_error.html')


def generar_comprobante(request, venta_id, token):
    # SEGURIDAD: solo se accede con el token único de la venta
    venta = get_object_or_404(Venta, id=venta_id, token=token)
    detalles = venta.detalleventa_set.all()

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="comprobante_venta_{venta.id}.pdf"'

    pdf = canvas.Canvas(response, pagesize=letter)
    ancho, alto = letter

    azul = colors.HexColor('#3483fa')
    verde = colors.HexColor('#16a34a')
    gris = colors.HexColor('#666666')
    gris_claro = colors.HexColor('#eeeeee')

    # El logo vive en static/img/logo.png. Con finders se lo encuentra
    # tanto en desarrollo como en producción (los media están en
    # Cloudinary y NO existen en el disco de Render).
    logo_path = finders.find('img/logo.png')
    if not logo_path:
        logo_path = os.path.join(settings.STATIC_ROOT or '', 'img', 'logo.png')
    if logo_path and os.path.exists(logo_path):
        pdf.drawImage(ImageReader(logo_path), 50, alto - 90, width=55, height=55, mask='auto')

    pdf.setFont("Helvetica-Bold", 20)
    pdf.setFillColor(azul)
    pdf.drawString(115, alto - 55, "Forrajería Rox")

    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(gris)
    pdf.drawString(115, alto - 72, "Todo para tus mascotas")

    pdf.setStrokeColor(gris_claro)
    pdf.setLineWidth(1)
    pdf.line(50, alto - 100, ancho - 50, alto - 100)

    y = alto - 130
    pdf.setFont("Helvetica-Bold", 14)
    pdf.setFillColor(colors.black)
    pdf.drawString(50, y, f"Comprobante de venta N° {venta.id}")

    y -= 22
    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(gris)
    pdf.drawString(50, y, f"Fecha: {timezone.localtime(venta.fecha).strftime('%d/%m/%Y %H:%M')}")

    y -= 16
    metodo_entrega_pdf = (
        'Envío a domicilio'
        if venta.metodo_entrega == 'envio'
        else 'Retiro en local'
    )
    pdf.drawString(50, y, f"Método de entrega: {metodo_entrega_pdf}")

    y -= 16
    if venta.pagada:
        estado_pago_pdf = 'Pagado'
    elif venta.confirmada:
        estado_pago_pdf = 'Pendiente de pago'
    else:
        estado_pago_pdf = 'Pendiente de confirmación'
    pdf.drawString(50, y, f"Estado: {estado_pago_pdf}")

    if venta.direccion and venta.direccion != 'no especificada':
        y -= 16
        pdf.drawString(50, y, f"Dirección: {venta.direccion}")

    if venta.mp_payment_id:
        y -= 16
        pdf.drawString(50, y, f"ID de pago Mercado Pago: {venta.mp_payment_id}")

    y -= 35
    pdf.setFillColor(azul)
    pdf.rect(50, y - 6, ancho - 100, 22, fill=True, stroke=False)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(58, y, "Producto")
    pdf.drawString(320, y, "Cant.")
    pdf.drawString(380, y, "P. Unit.")
    pdf.drawString(460, y, "Subtotal")

    y -= 26
    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(colors.black)

    for i, detalle in enumerate(detalles):
        if y < 100:
            pdf.showPage()
            y = alto - 60

        if i % 2 == 0:
            pdf.setFillColor(colors.HexColor('#fafafa'))
            pdf.rect(50, y - 6, ancho - 100, 20, fill=True, stroke=False)
            pdf.setFillColor(colors.black)

        nombre = detalle.producto.nombre
        if detalle.variante:
            opciones = " - ".join(o.nombre for o in detalle.variante.opciones.all())
            nombre = f"{nombre} ({opciones})"

        if len(nombre) > 42:
            nombre = nombre[:39] + "..."

        precio_unit = detalle.subtotal / detalle.cantidad

        pdf.drawString(58, y, nombre)
        pdf.drawString(320, y, str(detalle.cantidad))
        pdf.drawString(380, y, f"${precio_unit:.2f}")
        pdf.drawString(460, y, f"${detalle.subtotal:.2f}")
        y -= 22

    y -= 12
    pdf.setStrokeColor(gris_claro)
    pdf.line(50, y, ancho - 50, y)
    y -= 28

    pdf.setFont("Helvetica-Bold", 14)
    pdf.setFillColor(verde)
    pdf.drawRightString(ancho - 50, y, f"TOTAL: ${venta.total}")

    pdf.setFont("Helvetica", 8)
    pdf.setFillColor(gris)
    pdf.drawCentredString(ancho / 2, 40, "Gracias por tu compra — Forrajería Rox")
    pdf.drawCentredString(ancho / 2, 28, "Este comprobante es un resumen de tu pedido, no reemplaza factura fiscal.")

    pdf.save()
    return response


@require_POST
def vaciar_carrito(request):
    session_key = request.session.session_key
    Carrito.objects.filter(session_key=session_key).delete()
    return redirect('/carrito/')


def comprobante(request, venta_id, token):
    # SEGURIDAD: solo se accede con el token único de la venta
    venta = get_object_or_404(Venta, id=venta_id, token=token)
    detalles = venta.detalleventa_set.all()
    if venta.pagada:
        estado_pago = 'Pagado'
    elif venta.confirmada:
        estado_pago = 'Pendiente de pago'
    else:
        estado_pago = 'Pendiente de confirmación'
    return render(request, 'carrito/comprobante.html', {
        'venta': venta,
        'detalles': detalles,
        'estado_pago': estado_pago,
    })