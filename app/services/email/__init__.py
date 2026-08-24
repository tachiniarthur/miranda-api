"""
Envio de e-mail transacional.

Dois arquivos com responsabilidades separadas de propósito: `sender.py` sabe
COMO entregar (SMTP, Resend, log) e `messages.py` sabe O QUE dizer. O conteúdo
muda por motivo de produto; o transporte muda por motivo de infraestrutura.
"""
