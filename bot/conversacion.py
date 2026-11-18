"""Lógica del bot: decide qué contestar a cada mensaje. No sabe nada de HTTP ni de WhatsApp en sí.

Orden de decisión para cada mensaje de un cliente:
  1. Mensaje repetido (Meta reenvía avisos)        -> se ignora
  2. Comando del dueño (#reactivar, #pausar, ...)  -> se ejecuta
  3. Chat pausado (lo atiende una persona)          -> el bot no contesta
  4. Pasó el máximo de mensajes por hora           -> aviso una sola vez, después silencio
  5. Pide hablar con una persona                    -> derivación + aviso al dueño
  6. Tope diario de tokens alcanzado o IA caída     -> respuesta de respaldo con botón
  7. La IA contesta; si no sabe ([DERIVAR]) o menciona un precio que no está en negocio.md -> derivación
"""

import logging
import re
import uuid
from datetime import datetime, time, timedelta

from .almacen import Almacen, hace_una_hora
from .conocimiento import armar_prompt, precios_inventados
from .ia import MARCA_DERIVAR, ErrorIA, Proveedor
from .idioma import detectar_idioma, mensaje
from .whatsapp import Entrante, Salida

log = logging.getLogger("bot.conversacion")

BOTON_PERSONA = "humano"

_PIDE_PERSONA = re.compile(
    r"(hablar|comunicarme|contactar|pasame|pasar|atienda|atiende|quiero)\b.{0,25}\b(persona|humano|asesor|asesora|"
    r"alguien|vendedor|vendedora|operador|operadora|agente)"
    r"|(talk|speak|chat|connect)\b.{0,25}\b(human|person|agent|someone|representative)"
    r"|(falar|conversar|atendimento)\b.{0,25}\b(pessoa|humano|atendente|algu[eé]m)"
    r"|^\s*(humano|asesor|persona|agente|operador|human|agent|atendente)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def pide_persona(texto: str) -> bool:
    return bool(_PIDE_PERSONA.search(texto or ""))


def _rango_dias(texto: str) -> set[int]:
    dias: set[int] = set()
    for parte in texto.replace(" ", "").split(","):
        if "-" in parte:
            a, b = parte.split("-", 1)
            dias.update(range(int(a), int(b) + 1))
        elif parte:
            dias.add(int(parte))
    return dias


class Bot:
    def __init__(self, ia: Proveedor, almacen: Almacen, negocio: str, *, dueno_telefono: str = "",
                 historial_mensajes: int = 10, max_mensajes_hora: int = 20, horas_pausa: float = 12,
                 max_tokens_respuesta: int = 300, tope_tokens_dia: int = 0,
                 horario: str = "09:00-18:00", dias_habiles: str = "0-4"):
        self.ia = ia
        self.almacen = almacen
        self.negocio = negocio
        self.sistema = armar_prompt(negocio)
        self.dueno = dueno_telefono.lstrip("+")
        self.historial_mensajes = historial_mensajes
        self.max_mensajes_hora = max_mensajes_hora
        self.horas_pausa = horas_pausa
        self.max_tokens = max_tokens_respuesta
        self.tope_tokens_dia = tope_tokens_dia
        self.horario = horario
        inicio, fin = horario.split("-")
        self._abre, self._cierra = time.fromisoformat(inicio.strip()), time.fromisoformat(fin.strip())
        self._dias = _rango_dias(dias_habiles)

    @classmethod
    def desde_config(cls, config, ia: Proveedor, almacen: Almacen, negocio: str) -> "Bot":
        return cls(ia, almacen, negocio, dueno_telefono=config.dueno_telefono,
                   historial_mensajes=config.historial_mensajes, max_mensajes_hora=config.max_mensajes_hora,
                   horas_pausa=config.horas_pausa, max_tokens_respuesta=config.max_tokens_respuesta,
                   tope_tokens_dia=config.tope_tokens_dia, horario=config.horario, dias_habiles=config.dias_habiles)

    # ------------------------------------------------------------------ entrada principal
    def procesar(self, m: Entrante, ahora: datetime) -> list[Salida]:
        """Recibe un mensaje y devuelve los mensajes a enviar (al cliente y, si corresponde, al dueño)."""
        tel = m.telefono
        if self.almacen.ya_procesado(m.id):
            log.info("Mensaje %s repetido, se ignora", m.id)
            return []

        if self.dueno and tel == self.dueno and m.texto.strip().startswith("#"):
            self.almacen.guardar_mensaje(m.id, tel, "entrante", m.texto, ahora, "dueno")
            return [Salida(tel, self.comando_dueno(m.texto.strip(), ahora))]

        self.almacen.tocar(tel, m.nombre, ahora)
        if not self.almacen.guardar_mensaje(m.id, tel, "entrante", m.texto, ahora, "cliente"):
            return []
        idioma = detectar_idioma(m.texto)

        if self.almacen.pausada(tel, ahora):
            log.info("Chat %s pausado (lo atiende una persona): el bot no contesta", tel)
            return []

        recibidos = self.almacen.mensajes_desde(tel, hace_una_hora(ahora))
        if self.max_mensajes_hora and recibidos > self.max_mensajes_hora:
            log.warning("Cliente %s pasó el límite de %s mensajes por hora (%s)", tel, self.max_mensajes_hora, recibidos)
            if recibidos == self.max_mensajes_hora + 1:  # avisa una sola vez
                return [self._responder(tel, mensaje("limite", idioma), ahora, "sistema")]
            return []

        if m.boton_id == BOTON_PERSONA or pide_persona(m.texto):
            return self.derivar(m, ahora, idioma, "el cliente pidió hablar con una persona")

        if m.tipo not in ("text", "interactive", "button"):
            texto = {"en": "For now I can only read text messages. Could you write it to me?",
                     "pt": "Por enquanto só consigo ler mensagens de texto. Pode me escrever?"}.get(
                idioma, "Por ahora solo puedo leer mensajes de texto. ¿Me lo escribís?")
            return [self._responder(tel, texto, ahora, "sistema")]

        respuesta = self.consultar_ia(tel, ahora)
        if respuesta is None:
            return [self._responder(tel, mensaje("respaldo", idioma), ahora, "sistema",
                                    botones=((BOTON_PERSONA, mensaje("boton_persona", idioma)),))]

        if MARCA_DERIVAR in respuesta:
            texto_ia = respuesta.replace(MARCA_DERIVAR, "").strip()
            return self.derivar(m, ahora, idioma, "la IA no tenía la respuesta", texto_cliente=texto_ia or None)

        inventados = precios_inventados(respuesta, self.negocio)
        if inventados:
            log.warning("La IA mencionó precios que no están en negocio.md (%s). Se deriva en vez de enviarlo: %r",
                        ", ".join(sorted(inventados)), respuesta)
            return self.derivar(m, ahora, idioma, "la IA dio un precio que no está en negocio.md")

        return [self._responder(tel, respuesta, ahora, "ia")]

    # ------------------------------------------------------------------ IA
    def consultar_ia(self, telefono: str, ahora: datetime) -> str | None:
        """Pide la respuesta a la IA con el historial. Devuelve None si no se puede (tope o error)."""
        dia = ahora.date().isoformat()
        if self.tope_tokens_dia and self.almacen.tokens_del_dia(dia) >= self.tope_tokens_dia:
            log.warning("Se alcanzó el tope diario de %s tokens: respuesta de respaldo", self.tope_tokens_dia)
            return None
        historial = self.almacen.historial(telefono, self.historial_mensajes)
        try:
            r = self.ia.responder(self.sistema, historial, self.max_tokens)
        except ErrorIA as e:
            log.error("La IA falló para %s: %s", telefono, e)
            return None
        except Exception:  # noqa: BLE001 - cualquier error inesperado también usa la respuesta de respaldo
            log.exception("Error inesperado de la IA para %s", telefono)
            return None
        self.almacen.sumar_tokens(dia, r.tokens)
        log.info("IA (%s) respondió a %s con %s tokens", self.ia.nombre, telefono, r.tokens)
        return r.texto

    # ------------------------------------------------------------------ derivación
    def en_horario(self, ahora: datetime) -> bool:
        return ahora.weekday() in self._dias and self._abre <= ahora.time() < self._cierra

    def derivar(self, m: Entrante, ahora: datetime, idioma: str, motivo: str,
                texto_cliente: str | None = None) -> list[Salida]:
        """Pausa el bot en este chat, avisa al cliente y al dueño."""
        tel = m.telefono
        self.almacen.pausar(tel, ahora + timedelta(hours=self.horas_pausa), motivo)
        log.info("Chat %s derivado a una persona: %s", tel, motivo)
        if not texto_cliente:
            clave = "derivado" if self.en_horario(ahora) else "derivado_fuera_horario"
            texto_cliente = mensaje(clave, idioma, horario=self.horario)
        salidas = [self._responder(tel, texto_cliente, ahora, "sistema")]
        if self.dueno:
            nombre = f"{m.nombre} " if m.nombre else ""
            aviso = (f"Chat derivado: {nombre}+{tel}\nMotivo: {motivo}\nÚltimo mensaje: \"{m.texto[:300]}\"\n"
                     f"El bot queda pausado {self.horas_pausa:g} h en ese chat. Para reactivarlo: #reactivar {tel}")
            salidas.append(Salida(self.dueno, aviso))
        else:
            log.warning("No hay DUENO_TELEFONO configurado: nadie recibe el aviso de derivación de %s", tel)
        return salidas

    # ------------------------------------------------------------------ comandos del dueño
    def comando_dueno(self, texto: str, ahora: datetime) -> str:
        partes = texto.split()
        comando = partes[0].lower()
        argumento = partes[1].lstrip("+") if len(partes) > 1 else ""
        if comando == "#reactivar" and argumento:
            if not self.almacen.existe(argumento):
                return f"No encontré un chat con {argumento}."
            self.almacen.pausar(argumento, None)
            log.info("El dueño reactivó el bot para %s", argumento)
            return f"Listo, el bot vuelve a contestar en el chat de +{argumento}."
        if comando == "#pausar" and argumento:
            horas = float(partes[2]) if len(partes) > 2 and partes[2].replace(".", "", 1).isdigit() else self.horas_pausa
            self.almacen.pausar(argumento, ahora + timedelta(hours=horas), "pausado por el dueño")
            return f"Listo, el bot queda pausado {horas:g} h en el chat de +{argumento}."
        if comando == "#pausados":
            lista = self.almacen.pausadas(ahora)
            return "Chats pausados: " + ", ".join(f"+{t}" for t in lista) if lista else "No hay chats pausados."
        return ("Comandos: #reactivar <teléfono>, #pausar <teléfono> [horas], #pausados. "
                "Ejemplo: #reactivar 5491122334455")

    # ------------------------------------------------------------------ utilidades
    def _responder(self, telefono: str, texto: str, ahora: datetime, origen: str,
                   botones: tuple[tuple[str, str], ...] = ()) -> Salida:
        self.almacen.guardar_mensaje(f"bot-{uuid.uuid4().hex}", telefono, "saliente", texto, ahora, origen)
        return Salida(telefono, texto, botones)
