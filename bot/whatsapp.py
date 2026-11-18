"""Conexión con la API oficial de WhatsApp (Cloud API de Meta): leer avisos del webhook y enviar mensajes."""

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

log = logging.getLogger("bot.whatsapp")


@dataclass(frozen=True)
class Entrante:
    id: str
    telefono: str
    nombre: str | None
    texto: str
    boton_id: str | None
    fecha: datetime
    tipo: str = "text"


@dataclass(frozen=True)
class Salida:
    para: str
    texto: str
    botones: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (id, título)


def firma_valida(cuerpo: bytes, firma: str | None, app_secret: str) -> bool:
    """Meta firma cada aviso con HMAC-SHA256 del cuerpo usando el secreto de la app."""
    if not app_secret or not firma or not firma.startswith("sha256="):
        return False
    esperada = hmac.new(app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma.removeprefix("sha256="))


def leer_mensajes(aviso: dict) -> list[Entrante]:
    """Saca los mensajes de un aviso del webhook. Ignora estados de entrega (sent, delivered, read)."""
    mensajes = []
    for entrada in aviso.get("entry", []) or []:
        for cambio in entrada.get("changes", []) or []:
            valor = cambio.get("value", {}) or {}
            nombres = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in valor.get("contacts", []) or []}
            for m in valor.get("messages", []) or []:
                if "id" not in m or "from" not in m:
                    log.warning("Mensaje sin id o remitente, se ignora: %s", m)
                    continue
                tipo = m.get("type", "desconocido")
                texto, boton = "", None
                if tipo == "text":
                    texto = (m.get("text") or {}).get("body", "")
                elif tipo == "interactive":
                    inter = m.get("interactive") or {}
                    respuesta = inter.get("button_reply") or inter.get("list_reply") or {}
                    texto, boton = respuesta.get("title", ""), respuesta.get("id")
                elif tipo == "button":
                    texto = boton = (m.get("button") or {}).get("text")
                else:
                    texto = f"[{tipo}]"  # audio, imagen, ubicación, sticker...
                fecha = datetime.fromtimestamp(int(m.get("timestamp", 0) or 0), tz=timezone.utc)
                mensajes.append(Entrante(m["id"], m["from"], nombres.get(m["from"]), texto or "", boton, fecha, tipo))
    return mensajes


class ClienteWhatsApp:
    """Envía mensajes por la Cloud API. Se le puede pasar un httpx.Client falso para las pruebas."""

    def __init__(self, token: str, phone_number_id: str, api_version: str = "v21.0",
                 http: httpx.Client | None = None):
        self.url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        self.http = http or httpx.Client(timeout=15)
        self.encabezados = {"Authorization": f"Bearer {token}"}

    @staticmethod
    def cuerpo(salida: Salida) -> dict:
        base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": salida.para}
        if not salida.botones:
            return {**base, "type": "text", "text": {"preview_url": False, "body": salida.texto[:4096]}}
        return {**base, "type": "interactive", "interactive": {
            "type": "button",
            "body": {"text": salida.texto[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in salida.botones[:3]]},
        }}

    def enviar(self, salida: Salida) -> str | None:
        """Envía y devuelve el id del mensaje. Reintenta una vez si Meta está saturado (429/5xx)."""
        for intento in range(2):
            r = self.http.post(self.url, json=self.cuerpo(salida), headers=self.encabezados)
            if r.status_code < 400:
                return (r.json().get("messages") or [{}])[0].get("id")
            log.warning("WhatsApp respondió %s al enviar a %s: %s", r.status_code, salida.para, r.text[:300])
            if r.status_code not in (429, 500, 502, 503) or intento == 1:
                r.raise_for_status()
        return None


class ClienteConsola:
    """Cliente de WhatsApp para el modo demo: no envía nada, guarda lo que habría mandado."""

    def __init__(self):
        self.enviados: list[Salida] = []

    def enviar(self, salida: Salida) -> str:
        self.enviados.append(salida)
        log.info("[demo] a %s: %s", salida.para, salida.texto)
        return f"demo-{len(self.enviados)}"
