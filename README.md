# Chatbot de WhatsApp con IA para atención al cliente

Bot que contesta los mensajes de WhatsApp de un negocio usando IA (Claude de Anthropic, o OpenAI), con la
información real del negocio: productos, precios, horarios y políticas. Cuando no sabe algo o el cliente pide
una persona, pasa el chat a alguien del equipo y le avisa al dueño.

Usa la **API oficial de WhatsApp (Cloud API de Meta)**, no WhatsApp Web ni librerías no oficiales, así que el
número no corre riesgo de bloqueo.

## Qué resuelve

- Contesta al instante, las 24 horas, preguntas frecuentes: precios, horarios, envíos, medios de pago.
- **No inventa**: solo usa lo que está en `negocio.md`. Si el dato no está, lo dice y deriva. Además hay un
  control extra: si la respuesta de la IA menciona un precio que no figura en `negocio.md`, no se envía y el chat
  se deriva.
- **Contesta en el idioma del cliente** (español, inglés, portugués, etc.): traduce la información del negocio.
- **Derivación a una persona**: si el cliente lo pide ("quiero hablar con alguien", botón "Hablar con persona")
  o la IA no sabe. El bot se pausa en ese chat, el dueño recibe un aviso por WhatsApp, y después se reactiva
  con un comando (`#reactivar <tel>`), desde el panel, o solo después de `HORAS_PAUSA`.
- **Historial por cliente** en SQLite: la IA ve los últimos N mensajes, así entiende "¿y la grande?".
- **Límites para no gastar de más**: máximo de mensajes por cliente por hora, tope de tokens por respuesta y
  tope de tokens por día. Si la IA falla o se llega al tope, manda una respuesta de respaldo con botón para
  hablar con una persona.
- **Panel** `/admin/conversaciones` (protegido por clave): lista de chats, historial, uso de la IA, pausar,
  reactivar y responder como persona.

### Pedidos reales de Workana que cubre

- "Automatizar respuestas de WhatsApp con IA para mi tienda online"
- "Chatbot para ventas por WhatsApp que conteste precios y tome consultas"
- "Bot de WhatsApp con ChatGPT/Claude que responda preguntas frecuentes y derive a un asesor"
- "Asistente de WhatsApp que responda en varios idiomas (clientes de Brasil y EE. UU.)"
- "Integrar la API oficial de WhatsApp Business con un agente de IA"
- "Atención al cliente automática fuera de horario para una clínica / inmobiliaria / restaurante"

## Estructura

```
bot/
  app.py           servidor FastAPI: webhook de Meta, panel /admin, modo demo
  conversacion.py  lógica: qué contestar, derivación, pausa, límites, comandos del dueño
  ia.py            proveedores de IA (Anthropic, OpenAI, falso) con la misma interfaz
  conocimiento.py  carga negocio.md, arma el prompt de sistema, controla precios inventados
  idioma.py        detecta el idioma del cliente y traduce los mensajes fijos
  almacen.py       SQLite: conversaciones, mensajes, pausas, uso de tokens
  whatsapp.py      leer avisos del webhook, validar firma HMAC, enviar mensajes y botones
  config.py        variables de entorno
  simulador.py     chat por consola sin WhatsApp
negocio.md         la información del negocio (lo que el cliente edita)
tests/             58 pruebas, sin internet ni claves
```

## Probarlo en 1 minuto (sin cuentas ni claves)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt     # en Linux/Mac: .venv/bin/python
.venv/Scripts/python.exe -m bot.simulador --guion --falsa
```

Corre una conversación de ejemplo: preguntas con precio, un cliente en portugués y otro en inglés, una pregunta
que el bot no sabe (deriva y avisa al dueño), el chat pausado, el dueño que lo reactiva y un pedido de persona.

Para chatear vos: `.venv/Scripts/python.exe -m bot.simulador --falsa`. Comandos: `/dueno #reactivar <tel>`,
`/cliente <tel>`, `/persona`, `/salir`.

Con IA real: poné `ANTHROPIC_API_KEY=...` en el `.env` y corré `python -m bot.simulador` (sin `--falsa`).

La **IA falsa** no traduce ni redacta: busca en `negocio.md` la línea que más se parece a la pregunta y marca el
idioma detectado (`[en inglés] ...`). Sirve para probar todo el flujo sin gastar. La IA real sí traduce y redacta.

## Instalar y configurar

1. `python -m venv .venv` y `.venv/Scripts/python.exe -m pip install -r requirements.txt` (Python 3.12).
2. Copiar `.env.example` a `.env` y completar (cada variable está explicada ahí).
3. Editar `negocio.md` con los datos reales del negocio.
4. En [developers.facebook.com](https://developers.facebook.com): crear una app de tipo Business, agregar el
   producto WhatsApp, copiar el token y el Phone Number ID. Para producción, generar un token permanente
   (usuario del sistema en el Business Manager).
5. Levantar el servidor y configurar el webhook en Meta: URL `https://tu-dominio/webhook`, el mismo
   `WHATSAPP_VERIFY_TOKEN`, y suscribirse al campo `messages`.

Correr el servidor:

```bash
.venv/Scripts/python.exe -m uvicorn bot.app:crear_app_por_defecto --factory --port 8000
```

Endpoints:

| Ruta | Para qué |
|---|---|
| `GET /salud` | chequeo de vida (proveedor de IA y si cargó `negocio.md`) |
| `GET/POST /webhook` | verificación y avisos de Meta (con firma `X-Hub-Signature-256`) |
| `GET /admin/conversaciones` | panel HTML (el navegador pide usuario/clave: cualquier usuario + `ADMIN_CLAVE`) |
| `GET /admin/api/conversaciones` | lo mismo en JSON (`Authorization: Bearer <ADMIN_CLAVE>`) |
| `POST /demo/chat` | solo con `MODO_DEMO=1`: probar sin WhatsApp (`{"texto": "hola"}`) |

Comandos del dueño (desde el WhatsApp de `DUENO_TELEFONO` al número del bot):
`#reactivar 5491122334455`, `#pausar 5491122334455 4`, `#pausados`.

## Pruebas

```bash
.venv/Scripts/python.exe -m pytest
```

58 pruebas. No usan internet ni claves: las APIs de Anthropic, OpenAI y Meta se simulan con
`httpx.MockTransport`, y el servidor se prueba con el `TestClient` de FastAPI. Cubren: forma exacta de los
pedidos a Anthropic/OpenAI, reintentos, errores, firma del webhook, derivación, pausa y reactivación,
comandos del dueño, límite por hora, tope de tokens, respaldo, control de precios inventados, idiomas,
historial, panel con clave y simulador.

## Desplegar

Necesita una URL pública con HTTPS (Meta no llama a `localhost`).

- **Render / Railway**: subir el repo, comando de inicio del `Procfile`
  (`uvicorn bot.app:crear_app_por_defecto --factory --host 0.0.0.0 --port $PORT`), cargar las variables del
  `.env` en el panel del servicio. Usar un disco persistente para `datos/` (si no, el historial se borra en
  cada despliegue).
- **VPS (Ubuntu)**: clonar, crear el venv, correr con `uvicorn` detrás de nginx o Caddy (HTTPS automático) y un
  servicio de systemd para que reinicie solo.
- **Para probar el webhook desde tu PC**: `cloudflared tunnel --url http://localhost:8000` o ngrok, y pegar esa
  URL en Meta.

Costo aproximado de la IA: con `claude-haiku-5-5`, respuestas de 300 tokens y un `negocio.md` de una página,
cada respuesta usa unos pocos miles de tokens de entrada. Revisar precios vigentes en la web de Anthropic y
ajustar `TOPE_TOKENS_DIA`.

## CÓMO ADAPTARLO A UN CLIENTE

| # | Qué cambiar | Archivo | Tiempo |
|---|---|---|---|
| 1 | Cargar productos, precios, horarios, envíos, pagos y políticas del cliente | `negocio.md` | 30-60 min |
| 2 | Crear `.env` con token, Phone Number ID, verify token, app secret, número del dueño y clave de IA | `.env` | 15 min |
| 3 | Probar con el simulador 15-20 preguntas reales que recibe el cliente y ajustar `negocio.md` | `negocio.md` | 30 min |
| 4 | Tono del bot (tuteo/voseo/usted, emojis, firma), reglas especiales (ej. "nunca dar descuentos") | `bot/conocimiento.py` (`PROMPT_SISTEMA`) | 15-30 min |
| 5 | Textos fijos (derivación, respaldo, límite) y botón "Hablar con persona" | `bot/idioma.py` (`MENSAJES`) | 15 min |
| 6 | Horario de atención humana, horas de pausa, límites y topes | `.env` (`HORARIO`, `DIAS_HABILES`, `HORAS_PAUSA`, `MAX_MENSAJES_HORA`, `TOPE_TOKENS_DIA`) | 10 min |
| 7 | Palabras que disparan la derivación (ej. "reclamo", "factura") | `bot/conversacion.py` (`_PIDE_PERSONA`) | 15 min |
| 8 | Cambiar de modelo o proveedor (Claude Sonnet, OpenAI, OpenRouter) | `.env` (`PROVEEDOR_IA`, `*_MODELO`, `*_URL`) | 5 min |
| 9 | Desplegar, configurar el webhook en Meta y probar con el número real | Render/VPS + Meta | 1-2 h |
| 10 | Extras pagos aparte: catálogo desde Google Sheets, guardar pedidos, CRM, plantillas para escribir primero | nuevo módulo | 2-8 h c/u |

Total típico: **2-6 horas** (sin extras). Lo que más tarda suele ser la verificación de la cuenta de Meta del
cliente, que depende de él.

## Qué preguntarle al cliente

1. ¿Ya tenés una cuenta de WhatsApp Business API (Meta) o usás la app de WhatsApp Business en el celular? El
   número que pase a la API deja de funcionar en la app común.
2. ¿Podés pasarme la lista de productos/servicios con precios, horarios, zonas de envío, medios de pago y
   políticas (cambios, devoluciones)? ¿Cada cuánto cambian los precios?
3. ¿Cuáles son las 10-20 preguntas que más te hacen por WhatsApp? Si podés, mandame capturas de chats reales.
4. ¿Cuándo querés que el bot pase el chat a una persona, y quién lo atiende? ¿A qué número avisamos?
5. ¿En qué idiomas escriben tus clientes y con qué tono les hablás (vos, tú, usted)?
6. ¿Tenés un presupuesto mensual para la IA? (para fijar los topes de uso)
7. ¿Dónde lo alojamos: tenés servidor o lo dejo en Render/Railway a tu nombre?
8. ¿Necesitás que tome pedidos o turnos y los guarde en algún lado (planilla, sistema, CRM)?

## Licencia

MIT, ver `LICENSE`.
