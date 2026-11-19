"""Filtros de template del catálogo."""
from django import template

register = template.Library()


@register.filter
def cloudinary_thumb(value, size="w_640,h_480,c_pad,b_auto,q_auto,f_auto"):
    """Inserta una transformación Cloudinary para entregar imágenes
    completas (pad, sin recortar) en un tamaño uniforme.

    c_pad reescala la imagen para que entre completa (sin crop) y
    rellena el espacio restante con el color de borde detectado.

    Ej.:  http://res.cloudinary.com/dwgxsfbcy/image/upload/v1/abc.png
    →     http://res.cloudinary.com/dwgxsfbcy/image/upload/w_640,h_480,c_pad,b_auto,q_auto,f_auto/v1/abc.png
    """
    if not value:
        return value
    marker = '/image/upload/'
    if marker in value:
        return value.replace(marker, f'/image/upload/{size}/', 1)
    return value