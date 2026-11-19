"""Envío de emails de la tienda.

Render (plan gratuito) bloquea el tráfico saliente a los puertos SMTP
25, 465 y 587 desde septiembre de 2025. Por eso, cuando hay una
BREVO_API_KEY configurada, enviamos por la API HTTPS de Brevo (puerto 443).
Si no hay clave, usamos el backend de Django (SMTP/Gmail en producción o
locmem en los tests).
"""

import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

BREVO_URL = 'https://api.brevo.com/v3/smtp/email'
TIMEOUT_SEGUNDOS = 15


def _usar_brevo():
    """Solo usamos Brevo en el backend SMTP real (no en tests/dev)."""
    return (
        bool(getattr(settings, 'BREVO_API_KEY', ''))
        and settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend'
    )


def enviar_email(asunto, mensaje, destinatarios, from_email=None):
    """Envía un email a una lista de destinatarios.

    Devuelve la cantidad de destinatarios aceptados. Si el proveedor
    rechaza el envío, lanza una excepción para que el llamador decida.
    """
    destinatarios = [email for email in destinatarios if email]
    if not destinatarios:
        return 0

    remitente = from_email or settings.DEFAULT_FROM_EMAIL
    if not remitente:
        raise ValueError(
            'Falta configurar DEFAULT_FROM_EMAIL (o EMAIL_HOST_USER) con un '
            'remitente verificado en Brevo.'
        )

    if _usar_brevo():
        payload = {
            'sender': {
                'name': getattr(
                    settings, 'EMAIL_REMITENTE_NOMBRE', 'Forrajería Rox'
                ),
                'email': remitente,
            },
            'to': [{'email': email} for email in destinatarios],
            'subject': asunto,
            'textContent': mensaje,
        }
        respuesta = requests.post(
            BREVO_URL,
            json=payload,
            headers={
                'api-key': settings.BREVO_API_KEY,
                'content-type': 'application/json',
                'accept': 'application/json',
            },
            timeout=TIMEOUT_SEGUNDOS,
        )
        if respuesta.status_code >= 400:
            logger.error(
                'Brevo rechazó el envío (%s): %s',
                respuesta.status_code,
                respuesta.text[:500],
            )
            raise RuntimeError(
                f'Brevo respondió {respuesta.status_code}: {respuesta.text[:200]}'
            )
        return len(destinatarios)

    return send_mail(
        subject=asunto,
        message=mensaje,
        from_email=remitente,
        recipient_list=destinatarios,
        fail_silently=False,
    )
