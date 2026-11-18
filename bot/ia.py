"""Proveedores de IA: Anthropic (Claude), OpenAI y uno falso para pruebas y demo.

Todos cumplen la misma interfaz `Proveedor.responder(sistema, mensajes, max_tokens) -> RespuestaIA`,
así que cambiar de proveedor es cambiar PROVEEDOR_IA en el .env.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from .idioma import detectar_idioma

log = logging.getLogger("bot.ia")

MARCA_DERIVAR = "[DERIVAR]"  # la IA la pone al principio cuando no sabe o el cliente quiere una persona
REINTENTABLES = {408, 429, 500, 502, 503, 504, 529}


class ErrorIA(RuntimeError):
    """La IA no pudo contestar (sin clave, sin conexión, error de la API, respuesta vacía...)."""


@dataclass(frozen=True)
class RespuestaIA:
    texto: str
    tokens_entrada: int = 0
    tokens_salida: int = 0

    @property
    def tokens(self) -> int:
        return self.tokens_entrada + self.tokens_salida


class Proveedor(Protocol):
    nombre: str

    def responder(self, sistema: str, mensajes: list[dict], max_tokens: int) -> RespuestaIA: ...


def normalizar_mensajes(mensajes: list[dict]) -> list[dict]:
    """Deja el historial como lo esperan las APIs: empieza con 'user' y alterna user/assistant.

    Junta mensajes seguidos del mismo rol (por ejemplo, el cliente mandó 3 mensajes seguidos).
    """
    limpios: list[dict] = []
    for m in mensajes:
        texto = (m.get("content") or "").strip()
        if not texto or m.get("role") not in ("user", "assistant"):
            continue
        if limpios and limpios[-1]["role"] == m["role"]:
            limpios[-1] = {"role": m["role"], "content": limpios[-1]["content"] + "\n" + texto}
        else:
            limpios.append({"role": m["role"], "content": texto})
    while limpios and limpios[0]["role"] != "user":
        limpios.pop(0)
    return limpios


def _post_con_reintento(http: httpx.Client, url: str, cuerpo: dict, encabezados: dict, servicio: str) -> dict:
    """POST con un reintento si la API está saturada. Convierte cualquier falla en ErrorIA."""
    for intento in range(2):
        try:
            r = http.post(url, json=cuerpo, headers=encabezados)
        except httpx.HTTPError as e:
            if intento == 0:
                log.warning("%s: error de conexión (%s), reintento", servicio, e)
                continue
            raise ErrorIA(f"{servicio}: no se pudo conectar ({e})") from e
        if r.status_code < 400:
            try:
                return r.json()
            except ValueError as e:
                raise ErrorIA(f"{servicio}: respuesta que no es JSON") from e
        detalle = r.text[:300]
        if r.status_code in REINTENTABLES and intento == 0:
            espera = min(float(r.headers.get("retry-after", "1") or 1), 5)
            log.warning("%s respondió %s, reintento en %.0fs", servicio, r.status_code, espera)
            time.sleep(espera)
            continue
        raise ErrorIA(f"{servicio} respondió {r.status_code}: {detalle}")
    raise ErrorIA(f"{servicio}: sin respuesta")  # no debería llegar acá


class ProveedorAnthropic:
    """Claude por la API de Mensajes (https://docs.anthropic.com), usando httpx directamente."""

    nombre = "anthropic"

    def __init__(self, api_key: str, modelo: str = "claude-haiku-5-5", url: str = "https://api.anthropic.com",
                 http: httpx.Client | None = None, timeout: float = 25):
        if not api_key:
            raise ErrorIA("Falta ANTHROPIC_API_KEY")
        self.modelo = modelo
        self.url = url.rstrip("/") + "/v1/messages"
        self.http = http or httpx.Client(timeout=timeout)
        self.encabezados = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def responder(self, sistema: str, mensajes: list[dict], max_tokens: int) -> RespuestaIA:
        cuerpo = {"model": self.modelo, "max_tokens": max_tokens, "system": sistema,
                  "messages": normalizar_mensajes(mensajes)}
        datos = _post_con_reintento(self.http, self.url, cuerpo, self.encabezados, "Anthropic")
        texto = "".join(b.get("text", "") for b in datos.get("content", []) if b.get("type") == "text").strip()
        if not texto:
            raise ErrorIA(f"Anthropic devolvió una respuesta vacía (stop_reason={datos.get('stop_reason')})")
        uso = datos.get("usage") or {}
        return RespuestaIA(texto, int(uso.get("input_tokens", 0)), int(uso.get("output_tokens", 0)))


class ProveedorOpenAI:
    """Modelos de OpenAI (o compatibles, como OpenRouter o un servidor local) por Chat Completions."""

    nombre = "openai"

    def __init__(self, api_key: str, modelo: str = "gpt-4.1-mini", url: str = "https://api.openai.com",
                 http: httpx.Client | None = None, timeout: float = 25):
        if not api_key:
            raise ErrorIA("Falta OPENAI_API_KEY")
        self.modelo = modelo
        self.url = url.rstrip("/") + "/v1/chat/completions"
        self.http = http or httpx.Client(timeout=timeout)
        self.encabezados = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}

    def responder(self, sistema: str, mensajes: list[dict], max_tokens: int) -> RespuestaIA:
        cuerpo = {"model": self.modelo, "max_completion_tokens": max_tokens,
                  "messages": [{"role": "system", "content": sistema}, *normalizar_mensajes(mensajes)]}
        datos = _post_con_reintento(self.http, self.url, cuerpo, self.encabezados, "OpenAI")
        try:
            texto = (datos["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as e:
            raise ErrorIA("OpenAI devolvió un formato inesperado") from e
        if not texto:
            raise ErrorIA("OpenAI devolvió una respuesta vacía")
        uso = datos.get("usage") or {}
        return RespuestaIA(texto, int(uso.get("prompt_tokens", 0)), int(uso.get("completion_tokens", 0)))


# --- IA falsa ---------------------------------------------------------------------------------------

_PALABRAS_VACIAS = set("""
a al algo como con cual cuando cuanto cuánto cuesta de del el en es esta este hay hola la las lo los me mi para
por que qué quiero sale se son su sus tenes tenés tienen tu un una y yo vale precio precios saber
the a an and are do does for how i is it me much my of on the to what you your have price cost hello hi
o os um uma e de do da que quanto custa voce você tem para preço olá oi
""".split())


class ProveedorFalso:
    """IA de mentira para pruebas y demo: no usa internet ni claves.

    Busca en el texto del negocio (que viene en el prompt de sistema) la línea que más palabras comparte
    con la última pregunta. Si no encuentra nada, deriva a una persona (como haría la IA real).
    """

    nombre = "falso"

    def __init__(self, falla: bool = False):
        self.falla = falla
        self.llamadas: list[dict] = []

    def responder(self, sistema: str, mensajes: list[dict], max_tokens: int) -> RespuestaIA:
        self.llamadas.append({"sistema": sistema, "mensajes": mensajes, "max_tokens": max_tokens})
        if self.falla:
            raise ErrorIA("IA falsa configurada para fallar")
        pregunta = next((m["content"] for m in reversed(mensajes) if m["role"] == "user"), "")
        idioma = detectar_idioma(pregunta)
        conocimiento = sistema.split("<negocio>")[-1].split("</negocio>")[0]
        # Se comparan las primeras 5 letras para que "empanada" encuentre "Empanadas"
        raiz = lambda texto: {p[:5] for p in re.findall(r"\w+", texto.lower())  # noqa: E731
                              if p not in _PALABRAS_VACIAS and len(p) > 2 and not p.isdigit()}
        palabras = raiz(pregunta)
        # Cada línea suma sus palabras (x2) y las del título de su sección: así "¿qué horarios tienen?"
        # encuentra las líneas que están bajo "## Horarios" aunque no digan "horario".
        secciones: list[tuple[str, list[str]]] = [("", [])]
        for linea in conocimiento.splitlines():
            if linea.lstrip().startswith("#"):  # los títulos no son respuestas, pero ayudan a encontrarlas
                secciones.append((linea.strip("# 	"), []))
            elif linea.strip(" -*	"):
                secciones[-1][1].append(linea.strip(" -*	"))
        mejor, puntos = "", 0
        for titulo, lineas in secciones:
            raiz_titulo = raiz(titulo)
            for linea in lineas:
                en_linea = len(palabras & raiz(linea))
                total = 2 * en_linea + len(palabras & raiz_titulo)
                if total > puntos:
                    # Si solo coincidió el título, se responde con la sección entera (hasta 4 líneas).
                    mejor, puntos = (linea if en_linea else " ".join(lineas[:4])), total
        if not mejor:
            texto = {"en": f"{MARCA_DERIVAR} I don't have that information. I'll pass you to a person.",
                     "pt": f"{MARCA_DERIVAR} Não tenho essa informação. Vou passar para uma pessoa."}.get(
                idioma, f"{MARCA_DERIVAR} No tengo ese dato. Te paso con una persona del equipo.")
        else:
            # La IA falsa no traduce: marca el idioma detectado para que se vea en la demo. La real sí traduce.
            prefijo = {"en": "[en inglés] ", "pt": "[en portugués] "}.get(idioma, "")
            texto = f"{prefijo}{mejor}"
        return RespuestaIA(texto, tokens_entrada=len(sistema) // 4, tokens_salida=len(texto) // 4)


def crear_proveedor(config, http: httpx.Client | None = None) -> Proveedor:
    """Arma el proveedor según la configuración."""
    if config.proveedor_ia == "falso":
        return ProveedorFalso()
    if config.proveedor_ia == "openai":
        return ProveedorOpenAI(config.openai_api_key, config.openai_modelo, config.openai_url, http, config.timeout_ia)
    return ProveedorAnthropic(config.anthropic_api_key, config.anthropic_modelo, config.anthropic_url, http,
                              config.timeout_ia)
