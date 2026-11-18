"""Detección simple del idioma del cliente y mensajes fijos traducidos.

La IA ya contesta en el idioma del cliente (se lo pide el prompt). Esto sirve para los mensajes que NO pasan
por la IA: derivación, límite de mensajes y respuesta de respaldo cuando la IA falla.
"""

import re

_PISTAS = {
    "es": set("el la los las de que y en un una por para con es está hola gracias quiero necesito cuánto cuanto "
              "precio tienen tenés hay buenas buen día dónde donde cómo como envío".split()),
    "en": set("the and is are you your i to of for with hello hi thanks please want need how much price do "
              "does can what where when shipping have".split()),
    "pt": set("o os as um uma de que e em para com é está olá oi obrigado obrigada quero preciso quanto "
              "preço vocês voce você tem onde como envio frete".split()),
}

MENSAJES = {
    "derivado": {
        "es": "Te paso con una persona del equipo. Te va a responder por este mismo chat.",
        "en": "I'm passing you to a person from our team. They will reply in this same chat.",
        "pt": "Vou passar você para uma pessoa da equipe. Ela vai responder neste mesmo chat.",
    },
    "derivado_fuera_horario": {
        "es": "Te paso con una persona del equipo. Ahora estamos fuera de horario ({horario}); te responden apenas abramos.",
        "en": "I'm passing you to a person from our team. We are closed now ({horario}); they will reply as soon as we open.",
        "pt": "Vou passar você para uma pessoa da equipe. Estamos fora do horário ({horario}); respondem assim que abrirmos.",
    },
    "respaldo": {
        "es": "Perdón, ahora no puedo responder. Si querés, tocá el botón y te atiende una persona.",
        "en": "Sorry, I can't answer right now. Tap the button and a person will help you.",
        "pt": "Desculpe, não consigo responder agora. Toque no botão e uma pessoa vai te atender.",
    },
    "limite": {
        "es": "Recibimos muchos mensajes seguidos. En un rato seguimos; si es urgente, pedí hablar con una persona.",
        "en": "We received many messages in a row. Let's continue in a while; if it's urgent, ask for a person.",
        "pt": "Recebemos muitas mensagens seguidas. Continuamos daqui a pouco; se for urgente, peça para falar com uma pessoa.",
    },
    "boton_persona": {"es": "Hablar con persona", "en": "Talk to a person", "pt": "Falar com pessoa"},
}


def detectar_idioma(texto: str, defecto: str = "es") -> str:
    """Devuelve 'es', 'en' o 'pt' según qué palabras comunes aparecen más. Ante la duda, el idioma por defecto."""
    palabras = re.findall(r"[a-záéíóúãõâêôçñü]+", (texto or "").lower())
    if not palabras:
        return defecto
    puntos = {idioma: sum(p in pistas for p in palabras) for idioma, pistas in _PISTAS.items()}
    # Letras propias de cada idioma desempatan
    if re.search(r"[ãõç]", texto.lower()):
        puntos["pt"] += 2
    if re.search(r"[ñ¿¡]", texto.lower()):
        puntos["es"] += 2
    mejor = max(puntos, key=lambda k: (puntos[k], k == defecto))
    return mejor if puntos[mejor] > 0 else defecto


def mensaje(clave: str, idioma: str, **datos) -> str:
    textos = MENSAJES[clave]
    return textos.get(idioma, textos["es"]).format(**datos)
