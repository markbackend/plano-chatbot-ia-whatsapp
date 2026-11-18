"""Fixtures compartidas. Ninguna prueba usa internet ni claves reales."""

import hashlib
import hmac
import json
from datetime import datetime
from pathlib import Path

import pytest

from bot.almacen import Almacen
from bot.config import Config
from bot.conversacion import Bot
from bot.ia import ProveedorFalso, RespuestaIA
from bot.whatsapp import Entrante

RAIZ = Path(__file__).resolve().parent.parent
NEGOCIO = (RAIZ / "negocio.md").read_text(encoding="utf-8")
DUENO = "5491199999999"
CLIENTE = "5491100000001"
MIERCOLES_11 = datetime(2026, 11, 18, 11, 0)   # dentro del horario de atención
DOMINGO_23 = datetime(2026, 11, 22, 23, 0)     # fuera de horario


class IAFija:
    """IA de prueba que siempre devuelve el texto indicado y guarda las llamadas."""

    nombre = "fija"

    def __init__(self, texto: str = "Hola, ¿en qué te ayudo?", tokens: int = 10):
        self.texto, self.tokens, self.llamadas = texto, tokens, []

    def responder(self, sistema, mensajes, max_tokens):
        self.llamadas.append({"sistema": sistema, "mensajes": mensajes, "max_tokens": max_tokens})
        return RespuestaIA(self.texto, self.tokens, 0)


@pytest.fixture
def almacen():
    return Almacen(":memory:")


@pytest.fixture
def crear_bot(almacen):
    def _crear(ia=None, **opciones):
        valores = {"dueno_telefono": DUENO, "historial_mensajes": 10, "max_mensajes_hora": 20, "horas_pausa": 12,
                   "max_tokens_respuesta": 300, "tope_tokens_dia": 0}
        valores.update(opciones)
        return Bot(ia or ProveedorFalso(), almacen, NEGOCIO, **valores)
    return _crear


_contador = 0


def entrante(texto: str, telefono: str = CLIENTE, boton: str | None = None, tipo: str = "text",
             id: str | None = None) -> Entrante:
    global _contador
    _contador += 1
    return Entrante(id or f"wamid.{_contador}", telefono, "Ana", texto, boton, MIERCOLES_11, tipo)


def aviso_meta(texto: str, telefono: str = CLIENTE, id: str = "wamid.A1") -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "123"},
        "contacts": [{"wa_id": telefono, "profile": {"name": "Ana"}}],
        "messages": [{"id": id, "from": telefono, "timestamp": "1763474400", "type": "text", "text": {"body": texto}}],
    }}]}]}


def firmar(cuerpo: bytes, secreto: str) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def config_prueba(tmp_path, **cambios) -> Config:
    valores = dict(token="tok", phone_number_id="123", verify_token="verificar", app_secret="secreto",
                   dueno_telefono=DUENO, proveedor_ia="falso", negocio=RAIZ / "negocio.md",
                   base_datos=tmp_path / "prueba.db", admin_clave="clave-admin", tope_tokens_dia=0)
    valores.update(cambios)
    return Config(**valores)


def json_bytes(datos: dict) -> bytes:
    return json.dumps(datos).encode()
