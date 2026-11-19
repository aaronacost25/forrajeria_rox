from django.contrib import admin
from .models import (
    Producto,
    Categoria,
    SubCategoria,
    GrupoVariante,
    OpcionVariante,
    VarianteProducto,
)


class VarianteProductoInline(admin.TabularInline):
    model = VarianteProducto
    extra = 1
    filter_horizontal = ("opciones",)
    fields = ("opciones", "precio_extra", "stock")  # CAMBIO: orden explícito, stock bien visible


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "precio", "stock")
    list_editable = ("precio", "stock")
    list_filter = ("categoria", "subcategoria", "destacado", "oferta")
    search_fields = ("nombre", "descripcion")
    autocomplete_fields = ("categoria", "subcategoria")  # CAMBIO
    inlines = [VarianteProductoInline]


@admin.register(Categoria)  # CAMBIO: antes era admin.site.register(Categoria) simple
class CategoriaAdmin(admin.ModelAdmin):
    search_fields = ("nombre",)  # necesario para que autocomplete_fields funcione


@admin.register(SubCategoria)  # CAMBIO
class SubCategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria")
    list_filter = ("categoria",)
    search_fields = ("nombre",)


@admin.register(GrupoVariante)  # CAMBIO
class GrupoVarianteAdmin(admin.ModelAdmin):
    search_fields = ("nombre",)


@admin.register(OpcionVariante)  # CAMBIO
class OpcionVarianteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "grupo")
    list_filter = ("grupo",)
    search_fields = ("nombre",)


@admin.register(VarianteProducto)  # CAMBIO: antes era register simple, sin list_display
class VarianteProductoAdmin(admin.ModelAdmin):
    list_display = ("producto", "__str__", "precio_extra", "stock")
    list_filter = ("producto",)
    search_fields = ("producto__nombre",)
    list_editable = ("precio_extra", "stock")  # CAMBIO: podés corregir stock sin entrar al detalle