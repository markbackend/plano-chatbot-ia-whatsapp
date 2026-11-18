"""Servidor FastAPI: webhook de Meta (con firma), panel /admin y modo demo."""

import base64
import json

import httpx
import pytest
from conftest import CLIENTE, DUENO, MIERCOLES_11, aviso_meta, config_prueba, firmar, json_bytes
from fastapi.testclient import TestClient

from bot.almacen import Almacen
from bot.app import crear_app
from bot.whatsapp import ClienteConsola, ClienteWhatsApp, Salida, leer_mensajes

ADMIN = {"Authorization": "Bearer clave-admin"}


@pytest.fixture
def entorno(tmp_path):
    cliente = ClienteConsola()
    almacen = Almacen(":memory:")
    app = crear_app(config_prueba(tmp_path, modo_demo=True), cliente=cliente, almacen=almacen,
                    reloj=lambda: MIERCOLES_11)
    return TestClient(app), cliente, almacen


def postear(http: TestClient, aviso: dict, secreto: str = "secreto"):
    cuerpo = json_bytes(aviso)
    return http.post("/webhook", content=cuerpo, headers={"X-Hub-Signature-256": firmar(cuerpo, secreto),
                                                          "Content-Type": "application/json"})


def test_salud(entorno):
    http, _, _ = entorno
    assert http.get("/salud").json() == {"estado": "ok", "ia": "falso", "negocio_cargado": True}


def test_verificacion_del_webhook(entorno):
    http, _, _ = entorno
    ok = http.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verificar", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    mal = http.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "42"})
    assert mal.status_code == 403


def test_webhook_rechaza_firma_invalida(entorno):
    http, cliente, _ = entorno
    assert postear(http, aviso_meta("hola"), secreto="otro").status_code == 401
    assert http.post("/webhook", content=b"{}").status_code == 401
    assert cliente.enviados == []


def test_webhook_json_invalido(entorno):
    http, _, _ = entorno
    r = http.post("/webhook", content=b"no es json", headers={"X-Hub-Signature-256": firmar(b"no es json", "secreto")})
    assert r.status_code == 400


def test_webhook_responde_con_ia(entorno):
    http, cliente, almacen = entorno
    r = postear(http, aviso_meta("¿Cuánto sale la pizza muzzarella?"))
    assert r.status_code == 200 and r.json() == {"ok": True, "recibidos": 1}
    assert cliente.enviados == [Salida(CLIENTE, "Pizza muzzarella grande: $12.500")]
    # Meta reenvía el mismo aviso: no se contesta dos veces
    postear(http, aviso_meta("¿Cuánto sale la pizza muzzarella?"))
    assert len(cliente.enviados) == 1
    assert almacen.conversaciones()[0]["nombre"] == "Ana"


def test_webhook_ignora_estados_de_entrega(entorno):
    http, cliente, _ = entorno
    estado = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]}
    assert postear(http, estado).json() == {"ok": True, "recibidos": 0}
    assert cliente.enviados == []


def test_webhook_derivacion_avisa_al_dueno(entorno):
    http, cliente, almacen = entorno
    postear(http, aviso_meta("¿Tienen sushi?"))
    assert [s.para for s in cliente.enviados] == [CLIENTE, DUENO]
    assert almacen.pausada(CLIENTE, MIERCOLES_11)


def test_un_error_al_enviar_no_rompe_el_webhook(tmp_path):
    class ClienteRoto:
        def enviar(self, salida):
            raise httpx.ConnectError("sin red")
    app = crear_app(config_prueba(tmp_path), cliente=ClienteRoto(), almacen=Almacen(), reloj=lambda: MIERCOLES_11)
    assert postear(TestClient(app), aviso_meta("hola")).status_code == 200


def test_admin_requiere_clave(entorno):
    http, _, _ = entorno
    assert http.get("/admin/conversaciones").status_code == 401
    assert http.get("/admin/api/conversaciones", headers={"Authorization": "Bearer mala"}).status_code == 401
    basica = base64.b64encode(b"admin:clave-admin").decode()
    assert http.get("/admin/conversaciones", headers={"Authorization": f"Basic {basica}"}).status_code == 200


def test_admin_deshabilitado_sin_clave(tmp_path):
    app = crear_app(config_prueba(tmp_path, admin_clave=""), cliente=ClienteConsola(), almacen=Almacen())
    assert TestClient(app).get("/admin/conversaciones", headers=ADMIN).status_code == 503


def test_admin_lista_reactiva_y_responde(entorno):
    http, cliente, almacen = entorno
    postear(http, aviso_meta("¿Tienen sushi?"))
    datos = http.get("/admin/api/conversaciones", headers=ADMIN).json()
    assert datos["conversaciones"][0]["telefono"] == CLIENTE
    assert datos["uso_ia"][0]["llamadas"] == 1
    pagina = http.get("/admin/conversaciones", headers=ADMIN)
    assert "pausado" in pagina.text and "No tengo ese dato" in pagina.text
    chat = http.get(f"/admin/conversaciones/{CLIENTE}", headers=ADMIN)
    assert "Reactivar bot" in chat.text
    # Una persona responde desde el panel
    r = http.post(f"/admin/conversaciones/{CLIENTE}/responder", data={"texto": "Hola Ana, no tenemos sushi"},
                  headers=ADMIN, follow_redirects=False)
    assert r.status_code == 303 and cliente.enviados[-1] == Salida(CLIENTE, "Hola Ana, no tenemos sushi")
    # Reactiva el bot
    r = http.post(f"/admin/conversaciones/{CLIENTE}/reactivar", headers=ADMIN, follow_redirects=False)
    assert r.status_code == 303 and not almacen.pausada(CLIENTE, MIERCOLES_11)
    detalle = http.get(f"/admin/api/conversaciones/{CLIENTE}", headers=ADMIN).json()
    assert detalle["pausada"] is False
    assert [m["origen"] for m in detalle["mensajes"]] == ["cliente", "sistema", "persona"]
    # Pausa manual
    http.post(f"/admin/conversaciones/{CLIENTE}/pausar", headers=ADMIN)
    assert almacen.pausada(CLIENTE, MIERCOLES_11)
    assert http.get("/admin/api/conversaciones/000", headers=ADMIN).status_code == 404


def test_panel_escapa_html(entorno):
    http, _, _ = entorno
    postear(http, aviso_meta("<script>alert(1)</script>"))
    assert "<script>alert" not in http.get(f"/admin/conversaciones/{CLIENTE}", headers=ADMIN).text


def test_modo_demo(entorno):
    http, _, _ = entorno
    r = http.post("/demo/chat", json={"texto": "Hi! Do you have delivery?", "telefono": "+14155550123"})
    assert r.status_code == 200
    assert r.json()["respuestas"][0]["texto"].startswith("[en inglés] Hacemos delivery")


def test_sin_modo_demo_no_existe_el_endpoint(tmp_path):
    app = crear_app(config_prueba(tmp_path), cliente=ClienteConsola(), almacen=Almacen())
    assert TestClient(app).post("/demo/chat", json={"texto": "hola"}).status_code == 404


# --- cliente de WhatsApp ---------------------------------------------------------------------------

def test_leer_mensajes_de_botones_y_otros_tipos():
    aviso = {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": "1", "profile": {"name": "Ana"}}], "messages": [
        {"id": "a", "from": "1", "timestamp": "1", "type": "interactive",
         "interactive": {"type": "button_reply", "button_reply": {"id": "humano", "title": "Hablar con persona"}}},
        {"id": "b", "from": "1", "timestamp": "1", "type": "audio", "audio": {"id": "x"}},
        {"from": "1", "type": "text"},  # sin id: se ignora
    ]}}]}]}
    m = leer_mensajes(aviso)
    assert [(x.boton_id, x.texto, x.tipo, x.nombre) for x in m] == [
        ("humano", "Hablar con persona", "interactive", "Ana"), (None, "[audio]", "audio", "Ana")]


def test_cliente_whatsapp_envia_texto_y_botones_y_reintenta():
    pedidos, respuestas = [], [httpx.Response(429), httpx.Response(200, json={"messages": [{"id": "wamid.OK"}]}),
                               httpx.Response(200, json={"messages": [{"id": "wamid.B"}]})]

    def manejar(pedido):
        pedidos.append(pedido)
        return respuestas.pop(0)
    cliente = ClienteWhatsApp("tok", "123", http=httpx.Client(transport=httpx.MockTransport(manejar)))
    assert cliente.enviar(Salida("549", "hola")) == "wamid.OK"
    assert len(pedidos) == 2 and pedidos[0].headers["authorization"] == "Bearer tok"
    assert str(pedidos[0].url) == "https://graph.facebook.com/v21.0/123/messages"
    cliente.enviar(Salida("549", "elegí", (("humano", "Hablar con persona"),)))
    cuerpo = json.loads(pedidos[-1].content)
    assert cuerpo["type"] == "interactive"
    assert cuerpo["interactive"]["action"]["buttons"][0]["reply"] == {"id": "humano", "title": "Hablar con persona"}


def test_cliente_whatsapp_error_definitivo():
    cliente = ClienteWhatsApp("tok", "123", http=httpx.Client(transport=httpx.MockTransport(
        lambda p: httpx.Response(400, json={"error": {"message": "bad"}}))))
    with pytest.raises(httpx.HTTPStatusError):
        cliente.enviar(Salida("549", "hola"))
