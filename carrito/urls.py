from django.urls import path

from . import views
from .views import agregar_carrito, pagar, ver_carrito

from .views import (
    agregar_carrito,
    ver_carrito,
    sumar_cantidad,
    restar_cantidad,
    eliminar_producto
)

urlpatterns = [
    path('', ver_carrito, name='ver_carrito'),
    path('agregar/<int:producto_id>/', agregar_carrito, name='agregar_carrito'),
    path('sumar/<int:item_id>/', sumar_cantidad, name='sumar_cantidad'),
    path('restar/<int:item_id>/', restar_cantidad, name='restar_cantidad'),
    path('eliminar/<int:item_id>/', eliminar_producto, name='eliminar_producto'),
    path('pagar/', pagar, name='pagar'),
    path('webhook/', views.webhook, name='webhook'),
    path('pago-exitoso/', views.pago_exitoso, name='pago_exitoso'),
    path('vaciar/', views.vaciar_carrito, name='vaciar_carrito'),
    path('comprobante/<int:venta_id>/<uuid:token>/', views.comprobante, name='comprobante'),
    path('comprobante-pdf/<int:venta_id>/<uuid:token>/', views.generar_comprobante, name='generar_comprobante'),
    path('elegir-cuotas/', views.elegir_cuotas, name='elegir_cuotas') , 
    path('confirmar-pedido/', views.confirmar_pedido_manual, name='confirmar_pedido_manual'),  # CAMBIO
]