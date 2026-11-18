"""Simulador por consola: chateá con el bot sin WhatsApp.

    python -m bot.simulador            -> IA real si hay ANTHROPIC_API_KEY en el .env, si no la IA falsa
    python -m bot.simulador --falsa    -> siempre la IA falsa (sin internet ni claves)
    python -m bot.simulador --guion    -> corre una conversación de ejemplo sola (demo de 1 minuto)

Comandos dentro del simulador:
    /dueno <texto>   escribir como el dueño (ej: /dueno #reactivar 5491100000001)
    /cliente <tel>   cambiar de cliente
    /persona         tocar el botón "Hablar con persona"
    /salir
"""

import argparse
import logging
import os
import sys
import uuid
from datetime import datetime

from . import config as cfg
from .almacen import Almacen
from .conocimiento import cargar_negocio
from .conversacion import BOTON_PERSONA, Bot
from .ia import ErrorIA, ProveedorAnthropic, ProveedorFalso
from .whatsapp import Entrante

DUENO = "5491199999999"
CLIENTE = "5491100000001"

GUION = [
    (CLIENTE, "Hola! ¿Abren los lunes?"),
    (CLIENTE, "¿Cuánto sale la pizza muzzarella?"),
    ("5511988887777", "Olá! Quanto custa a pizza muzzarella?"),
    ("14155550123", "Hi! Do you have delivery?"),
    (CLIENTE, "¿Tienen sushi?"),
    (CLIENTE, "¿Hola? ¿Me escuchan?"),
    (DUENO, "#pausados"),
    (DUENO, f"#reactivar {CLIENTE}"),
    (CLIENTE, "Perfecto, ¿qué medios de pago aceptan?"),
    ("5491133334444", "Quiero hablar con una persona por favor"),
]


def armar_bot(config: cfg.Config, forzar_falsa: bool) -> tuple[Bot, str]:
    clave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if clave and not forzar_falsa:
        ia, descripcion = ProveedorAnthropic(clave, config.anthropic_modelo, config.anthropic_url), \
            f"IA real: Anthropic {config.anthropic_modelo}"
    else:
        ia, descripcion = ProveedorFalso(), "IA falsa (sin internet ni claves)"
    negocio = cargar_negocio(config.negocio)
    bot = Bot(ia, Almacen(":memory:"), negocio, dueno_telefono=DUENO,
              historial_mensajes=config.historial_mensajes, max_mensajes_hora=config.max_mensajes_hora,
              horas_pausa=config.horas_pausa, max_tokens_respuesta=config.max_tokens_respuesta,
              tope_tokens_dia=config.tope_tokens_dia, horario=config.horario, dias_habiles=config.dias_habiles)
    return bot, descripcion


def enviar(bot: Bot, telefono: str, texto: str, ahora: datetime, escribir=print, boton: str | None = None) -> None:
    quien = "DUEÑO" if telefono == DUENO else f"+{telefono}"
    escribir(f"\n{quien}: {texto}")
    m = Entrante(f"sim-{uuid.uuid4().hex}", telefono, "Cliente de prueba", texto, boton, ahora)
    salidas = bot.procesar(m, ahora)
    if not salidas:
        escribir("   (el bot no contesta: chat pausado o límite de mensajes)")
    for s in salidas:
        destino = "al DUEÑO" if s.para == DUENO else f"a +{s.para}"
        botones = f"  [botones: {', '.join(t for _, t in s.botones)}]" if s.botones else ""
        escribir(f"   BOT {destino}: {s.texto}{botones}")


def main(argv: list[str] | None = None, leer=input, escribir=print, ahora: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulador del chatbot de WhatsApp con IA")
    parser.add_argument("--falsa", action="store_true", help="usar la IA falsa aunque haya ANTHROPIC_API_KEY")
    parser.add_argument("--guion", action="store_true", help="correr la conversación de ejemplo y salir")
    args = parser.parse_args(argv)
    for flujo in (sys.stdout, sys.stdin):  # la consola de Windows a veces no usa UTF-8 (tildes, ñ)
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.WARNING, format="   [log] %(levelname)s %(name)s: %(message)s")

    try:
        config = cfg.cargar()
        bot, descripcion = armar_bot(config, args.falsa)
    except (cfg.ErrorConfig, ErrorIA) as e:
        escribir(f"Error de configuración: {e}")
        return 1
    escribir(f"Simulador del chatbot ({descripcion}). Negocio: {config.negocio.name}. Dueño: +{DUENO}")

    if args.guion:
        momento = ahora or datetime(2026, 11, 18, 11, 0)  # miércoles 11 h: dentro del horario de atención
        for telefono, texto in GUION:
            enviar(bot, telefono, texto, momento, escribir)
        escribir("\nFin del guion. Probá vos con: python -m bot.simulador --falsa")
        return 0

    telefono = CLIENTE
    escribir("Escribí como cliente. Comandos: /dueno <texto>, /cliente <tel>, /persona, /salir")
    while True:
        try:
            texto = leer(f"+{telefono}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not texto:
            continue
        if texto == "/salir":
            break
        momento = ahora or datetime.now()
        if texto.startswith("/cliente "):
            telefono = texto.split(maxsplit=1)[1].lstrip("+")
            escribir(f"Ahora sos +{telefono}")
        elif texto.startswith("/dueno "):
            enviar(bot, DUENO, texto.split(maxsplit=1)[1], momento, escribir)
        elif texto == "/persona":
            enviar(bot, telefono, "Hablar con persona", momento, escribir, boton=BOTON_PERSONA)
        else:
            enviar(bot, telefono, texto, momento, escribir)
    escribir("Chau.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
