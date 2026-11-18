"""Servidor: webhook que Meta llama con cada mensaje, panel /admin y (opcional) modo demo."""

import base64
import html
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from . import config as cfg
from .almacen import Almacen
from .conocimiento import cargar_negocio
from .conversacion import Bot
from .ia import ErrorIA, Proveedor, crear_proveedor
from .whatsapp import ClienteConsola, ClienteWhatsApp, Entrante, Salida, firma_valida, leer_mensajes

log = logging.getLogger("bot.app")


class MensajeDemo(BaseModel):
    telefono: str = "5491100000001"
    texto: str
    nombre: str | None = "Cliente demo"


def crear_app(config: cfg.Config | None = None, cliente=None, almacen: Almacen | None = None,
              ia: Proveedor | None = None, reloj=None) -> FastAPI:
    config = config or cfg.cargar()
    almacen = almacen or Almacen(config.base_datos)
    ia = ia or crear_proveedor(config)
    negocio = cargar_negocio(config.negocio)
    bot = Bot.desde_config(config, ia, almacen, negocio)
    if cliente is None:
        if config.token and config.phone_number_id:
            cliente = ClienteWhatsApp(config.token, config.phone_number_id, config.api_version)
        else:
            log.warning("Sin WHATSAPP_TOKEN/WHATSAPP_PHONE_NUMBER_ID: los mensajes NO se envían, solo se registran")
            cliente = ClienteConsola()
    zona = ZoneInfo(config.zona_horaria)
    reloj = reloj or (lambda: datetime.now(zona).replace(tzinfo=None))

    app = FastAPI(title="Chatbot de WhatsApp con IA", version="1.0.0")
    app.state.bot, app.state.almacen, app.state.cliente = bot, almacen, cliente

    def enviar_todo(salidas) -> int:
        enviados = 0
        for salida in salidas:
            try:
                cliente.enviar(salida)
                enviados += 1
            except Exception:  # noqa: BLE001 - un error de envío no debe frenar el resto
                log.exception("No se pudo enviar el mensaje a %s", salida.para)
        return enviados

    def procesar_mensajes(mensajes: list[Entrante]) -> None:
        for mensaje in mensajes:
            try:
                enviar_todo(bot.procesar(mensaje, reloj()))
            except Exception:  # noqa: BLE001 - un mensaje con error no debe frenar a los demás
                log.exception("Error procesando el mensaje %s de %s", mensaje.id, mensaje.telefono)

    @app.get("/salud")
    def salud():
        return {"estado": "ok", "ia": ia.nombre, "negocio_cargado": bool(negocio)}

    # ------------------------------------------------------------------ webhook de Meta
    @app.get("/webhook", response_class=PlainTextResponse)
    def verificar(modo: str = Query(alias="hub.mode", default=""), token: str = Query(alias="hub.verify_token", default=""),
                  desafio: str = Query(alias="hub.challenge", default="")):
        """Meta llama acá una sola vez al configurar el webhook."""
        if modo == "subscribe" and config.verify_token and secrets.compare_digest(token, config.verify_token):
            return desafio
        log.warning("Verificación de webhook rechazada (modo=%s)", modo)
        raise HTTPException(403, "Token de verificación incorrecto")

    @app.post("/webhook")
    async def recibir(request: Request, tareas: BackgroundTasks,
                      firma: str | None = Header(default=None, alias="X-Hub-Signature-256")):
        cuerpo = await request.body()
        if not firma_valida(cuerpo, firma, config.app_secret):
            log.warning("Aviso con firma inválida rechazado")
            raise HTTPException(401, "Firma inválida")
        try:
            aviso = json.loads(cuerpo)
        except json.JSONDecodeError:
            raise HTTPException(400, "JSON inválido")
        mensajes = leer_mensajes(aviso)
        if mensajes:
            # Meta necesita un 200 rápido: la IA se consulta después de responder
            tareas.add_task(procesar_mensajes, mensajes)
        return {"ok": True, "recibidos": len(mensajes)}

    # ------------------------------------------------------------------ panel /admin
    def autorizado(authorization: str | None = Header(default=None)) -> None:
        """Acepta 'Bearer <clave>' (para scripts) o usuario/clave del navegador (Basic, cualquier usuario)."""
        if not config.admin_clave:
            raise HTTPException(503, "Panel deshabilitado: falta ADMIN_CLAVE en el .env")
        clave = ""
        if authorization and authorization.startswith("Bearer "):
            clave = authorization.removeprefix("Bearer ")
        elif authorization and authorization.startswith("Basic "):
            try:
                clave = base64.b64decode(authorization.removeprefix("Basic ")).decode().split(":", 1)[1]
            except (ValueError, IndexError, UnicodeDecodeError):
                clave = ""
        if not clave or not secrets.compare_digest(clave.encode(), config.admin_clave.encode()):
            raise HTTPException(401, "No autorizado", headers={"WWW-Authenticate": 'Basic realm="admin"'})

    @app.get("/admin/api/conversaciones", dependencies=[Depends(autorizado)])
    def api_conversaciones():
        return {"conversaciones": almacen.conversaciones(), "uso_ia": almacen.uso()}

    @app.get("/admin/api/conversaciones/{telefono}", dependencies=[Depends(autorizado)])
    def api_mensajes(telefono: str):
        if not almacen.existe(telefono):
            raise HTTPException(404, "No existe esa conversación")
        return {"telefono": telefono, "pausada": almacen.pausada(telefono, reloj()),
                "mensajes": almacen.mensajes_de(telefono)}

    @app.get("/admin/conversaciones", response_class=HTMLResponse, dependencies=[Depends(autorizado)])
    def panel():
        ahora = reloj()
        filas = "".join(
            f"<tr><td><a href='/admin/conversaciones/{html.escape(c['telefono'])}'>+{html.escape(c['telefono'])}</a></td>"
            f"<td>{html.escape(c['nombre'] or '')}</td><td>{html.escape((c['ultimo'] or '')[:80])}</td>"
            f"<td>{c['cantidad']}</td><td>{html.escape(c['actualizada'][:16].replace('T', ' '))}</td>"
            f"<td>{'<b>pausado</b>' if almacen.pausada(c['telefono'], ahora) else 'bot'}</td></tr>"
            for c in almacen.conversaciones())
        uso = "".join(f"<li>{u['dia']}: {u['tokens']} tokens en {u['llamadas']} respuestas</li>" for u in almacen.uso())
        return _pagina("Conversaciones",
                       f"<table><tr><th>Teléfono</th><th>Nombre</th><th>Último mensaje</th><th>Msjs</th>"
                       f"<th>Actualizada</th><th>Estado</th></tr>{filas or '<tr><td colspan=6>Sin conversaciones</td></tr>'}"
                       f"</table><h2>Uso de la IA</h2><ul>{uso or '<li>Sin uso todavía</li>'}</ul>")

    @app.get("/admin/conversaciones/{telefono}", response_class=HTMLResponse, dependencies=[Depends(autorizado)])
    def panel_chat(telefono: str):
        if not almacen.existe(telefono):
            raise HTTPException(404, "No existe esa conversación")
        pausada = almacen.pausada(telefono, reloj())
        burbujas = "".join(
            f"<div class='{m['direccion']}'><small>{html.escape(m['fecha'][:16].replace('T', ' '))} · "
            f"{html.escape(m['origen'] or '')}</small><br>{html.escape(m['texto'])}</div>"
            for m in almacen.mensajes_de(telefono))
        t = html.escape(telefono)
        acciones = (f"<form method='post' action='/admin/conversaciones/{t}/reactivar'><button>Reactivar bot</button></form>"
                    if pausada else
                    f"<form method='post' action='/admin/conversaciones/{t}/pausar'><button>Pausar bot</button></form>")
        responder = (f"<form method='post' action='/admin/conversaciones/{t}/responder'>"
                     f"<input name='texto' required placeholder='Responder como persona' style='width:70%'>"
                     f"<button>Enviar</button></form>")
        return _pagina(f"Chat con +{t}", f"<p><a href='/admin/conversaciones'>Volver</a> · Estado: "
                                         f"{'pausado (atiende una persona)' if pausada else 'responde el bot'}</p>"
                                         f"{acciones}<div class='chat'>{burbujas}</div>{responder}")

    @app.post("/admin/conversaciones/{telefono}/reactivar", dependencies=[Depends(autorizado)])
    def reactivar(telefono: str):
        if not almacen.existe(telefono):
            raise HTTPException(404, "No existe esa conversación")
        almacen.pausar(telefono, None)
        log.info("Bot reactivado desde el panel para %s", telefono)
        return RedirectResponse(f"/admin/conversaciones/{telefono}", status_code=303)

    @app.post("/admin/conversaciones/{telefono}/pausar", dependencies=[Depends(autorizado)])
    def pausar(telefono: str):
        if not almacen.existe(telefono):
            raise HTTPException(404, "No existe esa conversación")
        almacen.pausar(telefono, reloj() + timedelta(hours=config.horas_pausa), "pausado desde el panel")
        return RedirectResponse(f"/admin/conversaciones/{telefono}", status_code=303)

    @app.post("/admin/conversaciones/{telefono}/responder", dependencies=[Depends(autorizado)])
    def responder(telefono: str, texto: str = Form(...)):
        """Una persona responde desde el panel (el mensaje sale por la API del negocio)."""
        if not almacen.existe(telefono):
            raise HTTPException(404, "No existe esa conversación")
        try:
            cliente.enviar(Salida(telefono, texto))
        except Exception as e:  # noqa: BLE001
            log.exception("No se pudo enviar la respuesta manual a %s", telefono)
            raise HTTPException(502, f"No se pudo enviar por WhatsApp: {e}") from e
        almacen.guardar_mensaje(f"manual-{uuid.uuid4().hex}", telefono, "saliente", texto, reloj(), "persona")
        return RedirectResponse(f"/admin/conversaciones/{telefono}", status_code=303)

    # ------------------------------------------------------------------ modo demo (sin WhatsApp)
    if config.modo_demo:
        log.warning("MODO_DEMO activo: POST /demo/chat responde sin pasar por WhatsApp. No usar en producción.")

        @app.post("/demo/chat")
        def demo_chat(m: MensajeDemo):
            entrante = Entrante(f"demo-{uuid.uuid4().hex}", m.telefono.lstrip("+"), m.nombre, m.texto, None, reloj())
            salidas = bot.procesar(entrante, reloj())
            return {"respuestas": [{"para": s.para, "texto": s.texto, "botones": [t for _, t in s.botones]}
                                   for s in salidas]}

    return app


def _pagina(titulo: str, cuerpo: str) -> str:
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(titulo)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:20px auto;padding:0 16px;color:#222}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left}}
.chat div{{margin:6px 0;padding:8px 10px;border-radius:8px;max-width:75%}}
.entrante{{background:#f1f1f1}}.saliente{{background:#dcf8c6;margin-left:auto!important}}
small{{color:#777}}form{{margin:10px 0}}</style></head>
<body><h1>{html.escape(titulo)}</h1>{cuerpo}</body></html>"""


def crear_app_por_defecto() -> FastAPI:
    """Punto de entrada para uvicorn: `uvicorn bot.app:crear_app_por_defecto --factory`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return crear_app()
    except ErrorIA as error:
        # Mensaje claro en vez de un traceback: casi siempre es una clave que falta en el .env.
        log.error("No se pudo arrancar el bot: %s. Completá el .env (o usá PROVEEDOR_IA=falso para probar "
                  "sin clave).", error)
        raise SystemExit(1) from None
