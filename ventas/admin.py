from django.contrib import admin, messages

from carrito.models import DetalleVenta
from carrito.views import _confirmar_venta
from .models import Venta


class DetalleVentaInline(admin.TabularInline):
    model = DetalleVenta
    extra = 0
    readonly_fields = ('producto', 'variante', 'cantidad', 'subtotal')


@admin.register(Venta)
class VentaAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'fecha', 'total', 'metodo_pago',
        'metodo_entrega', 'confirmada', 'pagada', 'mp_payment_id',
    )
    list_filter = ('confirmada', 'pagada', 'metodo_pago', 'metodo_entrega')
    search_fields = ('id', 'mp_payment_id', 'direccion')
    readonly_fields = (
        'fecha', 'token', 'items_json', 'session_key',
        'confirmada', 'mp_payment_id',
    )
    date_hierarchy = 'fecha'
    inlines = [DetalleVentaInline]
    ordering = ('-fecha',)
    actions = ('marcar_pagadas',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        ya_pagada = False
        if change and obj.pk:
            ya_pagada = bool(
                Venta.objects.filter(pk=obj.pk)
                .values_list('pagada', flat=True)
                .first()
            )

        if obj.pagada and not ya_pagada and not obj.confirmada:
            obj.pagada = False
            _confirmar_venta(obj, marcar_pagada=True)
            obj.pagada = True
            messages.info(
                request,
                f'La venta N° {obj.id} se confirmó y se descontó el stock.',
            )

        super().save_model(request, obj, form, change)

    @admin.action(description='Marcar ventas como pagadas')
    def marcar_pagadas(self, request, queryset):
        cantidad = 0
        for venta in queryset.filter(pagada=False):
            if not venta.confirmada:
                _confirmar_venta(venta, marcar_pagada=True)
            else:
                venta.pagada = True
                venta.save(update_fields=['pagada'])
            cantidad += 1
        self.message_user(
            request,
            f'{cantidad} venta(s) marcada(s) como pagadas.',
        )