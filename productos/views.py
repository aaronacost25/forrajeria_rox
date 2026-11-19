from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db.models import Q

from carrito.models import Carrito
from .models import Producto, Categoria


def _carrito_count(request):
    """Ítems en el carrito de la sesión actual (no de todos los usuarios)."""
    session_key = request.session.session_key
    if not session_key:
        return 0
    return Carrito.objects.filter(session_key=session_key).count()


def lista_productos(request):
    busqueda = request.GET.get('buscar', '')

    productos = Producto.objects.all()
    categorias = Categoria.objects.all()

    if busqueda:
        productos = productos.filter(
            Q(nombre__icontains=busqueda) |
            Q(descripcion__icontains=busqueda)
        )
        destacados = Producto.objects.none()
    else:
        destacados = Producto.objects.filter(destacado=True)
        productos = Producto.objects.exclude(destacado=True)

    return render(request, 'productos/lista_productos.html', {
        'productos': productos,
        'categorias': categorias,
        'destacados': destacados,
        'busqueda': busqueda,
        'carrito_count': _carrito_count(request),
    })


def filtrar_subcategoria(request, subcategoria_id):
    productos = Producto.objects.filter(subcategoria_id=subcategoria_id)
    categorias = Categoria.objects.all()
    destacados = Producto.objects.none()

    return render(request, 'productos/lista_productos.html', {
        'productos': productos,
        'categorias': categorias,
        'destacados': destacados,
        'busqueda': '',
        'carrito_count': _carrito_count(request),
    })


def buscar_ajax(request):
    termino = request.GET.get('term', '')

    productos = Producto.objects.filter(
        Q(nombre__icontains=termino) |
        Q(descripcion__icontains=termino)
    )[:5]

    data = []

    for producto in productos:
        data.append({
            'id': producto.id,
            'nombre': producto.nombre,
            'precio': str(producto.precio),
            'imagen': producto.imagen.url if producto.imagen else '',
        })

    return JsonResponse(data, safe=False)


def detalle_producto(request, id):
    producto = get_object_or_404(Producto, id=id)
    variantes = producto.variantes.all().prefetch_related('opciones__grupo')

    return render(request, 'productos/detalle_producto.html', {
        'producto': producto,
        'variantes': variantes,
    })
