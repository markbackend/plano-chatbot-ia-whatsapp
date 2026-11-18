"""Proveedores de IA probados contra transportes HTTP falsos (sin internet ni claves reales)."""

import json

import httpx
import pytest

from bot import ia as modulo_ia
from bot.config import Config
from bot.ia import (MARCA_DERIVAR, ErrorIA, ProveedorAnthropic, ProveedorFalso, ProveedorOpenAI, crear_proveedor,
                    normalizar_mensajes)


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(modulo_ia.time, "sleep", lambda s: None)


def cliente_falso(respuestas: list, pedidos: list) -> httpx.Client:
    """httpx.Client que devuelve las respuestas en orden y guarda cada pedido."""
    cola = list(respuestas)

    def manejar(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        r = cola.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return httpx.Client(transport=httpx.MockTransport(manejar))


def respuesta_anthropic(texto="Hola!", entrada=50, salida=5):
    return httpx.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant",
                                     "content": [{"type": "text", "text": texto}], "stop_reason": "end_turn",
                                     "usage": {"input_tokens": entrada, "output_tokens": salida}})


def test_anthropic_arma_el_pedido_correcto():
    pedidos = []
    p = ProveedorAnthropic("clave-falsa", "claude-haiku-5-5", "https://api.anthropic.com",
                           http=cliente_falso([respuesta_anthropic("Abrimos a las 19")], pedidos))
    r = p.responder("sos un bot", [{"role": "user", "content": "¿a qué hora abren?"}], 200)
    assert r.texto == "Abrimos a las 19" and r.tokens == 55
    pedido = pedidos[0]
    assert str(pedido.url) == "https://api.anthropic.com/v1/messages"
    assert pedido.headers["x-api-key"] == "clave-falsa"
    assert pedido.headers["anthropic-version"] == "2023-06-01"
    cuerpo = json.loads(pedido.content)
    assert cuerpo == {"model": "claude-haiku-5-5", "max_tokens": 200, "system": "sos un bot",
                      "messages": [{"role": "user", "content": "¿a qué hora abren?"}]}


def test_anthropic_reintenta_si_esta_saturada():
    pedidos = []
    p = ProveedorAnthropic("k", http=cliente_falso([httpx.Response(529, json={"error": {"type": "overloaded_error"}}),
                                                    respuesta_anthropic("ok")], pedidos))
    assert p.responder("s", [{"role": "user", "content": "hola"}], 50).texto == "ok"
    assert len(pedidos) == 2


def test_anthropic_error_definitivo_lanza_error_ia():
    p = ProveedorAnthropic("k", http=cliente_falso([httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})], []))
    with pytest.raises(ErrorIA, match="401"):
        p.responder("s", [{"role": "user", "content": "hola"}], 50)


def test_anthropic_sin_conexion_lanza_error_ia():
    falla = httpx.ConnectError("sin red")
    p = ProveedorAnthropic("k", http=cliente_falso([falla, falla], []))
    with pytest.raises(ErrorIA, match="conectar"):
        p.responder("s", [{"role": "user", "content": "hola"}], 50)


def test_anthropic_respuesta_vacia_es_error():
    vacia = httpx.Response(200, json={"content": [], "stop_reason": "max_tokens", "usage": {}})
    p = ProveedorAnthropic("k", http=cliente_falso([vacia], []))
    with pytest.raises(ErrorIA, match="vacía"):
        p.responder("s", [{"role": "user", "content": "hola"}], 50)


def test_anthropic_sin_clave():
    with pytest.raises(ErrorIA, match="ANTHROPIC_API_KEY"):
        ProveedorAnthropic("")


def test_openai_arma_el_pedido_y_lee_la_respuesta():
    pedidos = []
    ok = httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Hi there"}}],
                                   "usage": {"prompt_tokens": 30, "completion_tokens": 3}})
    p = ProveedorOpenAI("sk-falsa", "gpt-4.1-mini", http=cliente_falso([ok], pedidos))
    r = p.responder("sistema", [{"role": "user", "content": "hello"}], 100)
    assert r.texto == "Hi there" and r.tokens == 33
    cuerpo = json.loads(pedidos[0].content)
    assert pedidos[0].headers["authorization"] == "Bearer sk-falsa"
    assert str(pedidos[0].url) == "https://api.openai.com/v1/chat/completions"
    assert cuerpo["messages"][0] == {"role": "system", "content": "sistema"}
    assert cuerpo["max_completion_tokens"] == 100


def test_openai_formato_inesperado():
    p = ProveedorOpenAI("k", http=cliente_falso([httpx.Response(200, json={"algo": 1})], []))
    with pytest.raises(ErrorIA):
        p.responder("s", [{"role": "user", "content": "hola"}], 50)


def test_normalizar_junta_roles_repetidos_y_empieza_con_user():
    mensajes = [{"role": "assistant", "content": "bienvenido"}, {"role": "user", "content": "hola"},
                {"role": "user", "content": "¿precio?"}, {"role": "assistant", "content": "$10"},
                {"role": "user", "content": "  "}]
    assert normalizar_mensajes(mensajes) == [{"role": "user", "content": "hola\n¿precio?"},
                                             {"role": "assistant", "content": "$10"}]


def test_ia_falsa_responde_con_el_negocio_o_deriva():
    sistema = "<negocio>\n- Pizza muzzarella: $100\n- Envíos a Palermo\n</negocio>"
    falsa = ProveedorFalso()
    assert falsa.responder(sistema, [{"role": "user", "content": "precio pizza muzzarella"}], 50).texto == \
        "Pizza muzzarella: $100"
    assert falsa.responder(sistema, [{"role": "user", "content": "¿hacen envío?"}], 50).texto == "Envíos a Palermo"
    assert falsa.responder(sistema, [{"role": "user", "content": "¿venden helado?"}], 50).texto.startswith(MARCA_DERIVAR)
    with pytest.raises(ErrorIA):
        ProveedorFalso(falla=True).responder(sistema, [], 50)


def test_crear_proveedor_segun_config():
    assert crear_proveedor(Config(proveedor_ia="falso")).nombre == "falso"
    assert crear_proveedor(Config(proveedor_ia="openai", openai_api_key="k")).nombre == "openai"
    p = crear_proveedor(Config(anthropic_api_key="k", anthropic_modelo="claude-sonnet-5-5"))
    assert p.nombre == "anthropic" and p.modelo == "claude-sonnet-5-5"
