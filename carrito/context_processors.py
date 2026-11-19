"""Context processor global: contador de ítems del carrito de la sesión actual."""
from carrito.models import Carrito


def carrito_count(request):
    session_key = request.session.session_key
    count = 0
    if session_key:
        count = Carrito.objects.filter(session_key=session_key).count()
    return {'carrito_count': count}