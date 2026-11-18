"""Base de conocimiento del negocio (negocio.md) y prompt de sistema para la IA."""

import logging
import re
from pathlib import Path

from .ia import MARCA_DERIVAR

log = logging.getLogger("bot.conocimiento")

PROMPT_SISTEMA = """Sos el asistente de atención al cliente por WhatsApp de este negocio.

Reglas (obligatorias):
1. Usá SOLO la información que está entre <negocio> y </negocio>. No inventes productos, precios, stock, plazos,
   direcciones ni políticas. Si el dato no está, decí que no lo tenés y empezá tu respuesta con {marca}.
2. Si el cliente pide hablar con una persona, quiere hacer un reclamo, o el tema requiere a alguien del equipo
   (pagos con problemas, devoluciones, casos especiales), empezá tu respuesta con {marca}.
3. Contestá en el MISMO idioma en que escribe el cliente (si escribe en inglés, en inglés; en portugués, en
   portugués). Traducí vos la información del negocio.
4. Respuestas cortas: 1 a 3 oraciones, tono amable y natural de WhatsApp. Sin listas largas, sin markdown.
5. No pidas datos sensibles (contraseñas, números de tarjeta completos).
6. Si te escriben algo que no tiene que ver con el negocio, redirigí la charla con amabilidad.

<negocio>
{negocio}
</negocio>"""


def cargar_negocio(ruta: Path) -> str:
    """Lee negocio.md. Si no existe, el bot igual arranca pero va a derivar casi todo (y lo avisa en el log)."""
    try:
        texto = Path(ruta).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        log.error("No existe el archivo del negocio %s: el bot no va a tener información para responder", ruta)
        return ""
    if not texto:
        log.error("El archivo del negocio %s está vacío", ruta)
    return texto


def armar_prompt(negocio: str) -> str:
    return PROMPT_SISTEMA.format(marca=MARCA_DERIVAR, negocio=negocio or "(sin información cargada)")


_PRECIO = re.compile(
    r"(?:(?:US\$|USD|U\$S|R\$|\$|€|ARS|BRL|EUR)\s?(\d[\d.,]*))|(?:(\d[\d.,]*)\s?(?:USD|US\$|ARS|BRL|EUR|pesos|dólares|dolares|dollars|reais|euros))",
    re.IGNORECASE,
)


def _solo_digitos(numero: str) -> str:
    """'12.500,00' -> '12500'; '1,250' -> '1250'. Ignora decimales en cero para comparar."""
    numero = re.sub(r"[.,]00$", "", numero.strip(".,"))
    return re.sub(r"\D", "", numero)


def precios_en(texto: str) -> set[str]:
    return {_solo_digitos(a or b) for a, b in _PRECIO.findall(texto or "") if _solo_digitos(a or b)}


def precios_inventados(respuesta: str, negocio: str) -> set[str]:
    """Precios que aparecen en la respuesta de la IA y NO están en negocio.md (control extra contra inventos)."""
    conocidos = {_solo_digitos(n) for n in re.findall(r"\d[\d.,]*", negocio or "")}
    return {p for p in precios_en(respuesta) if p not in conocidos}
