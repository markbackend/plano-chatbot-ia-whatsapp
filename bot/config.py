"""Configuración desde variables de entorno (.env). Ninguna clave va escrita en el código."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent


class ErrorConfig(ValueError):
    """Una variable de entorno tiene un valor inválido."""


@dataclass(frozen=True)
class Config:
    # --- WhatsApp (Cloud API de Meta) ---
    token: str = ""                 # token de acceso de la app de Meta
    phone_number_id: str = ""       # ID del número de WhatsApp Business
    verify_token: str = ""          # palabra secreta para verificar el webhook
    app_secret: str = ""            # secreto de la app, para validar la firma de cada aviso
    api_version: str = "v21.0"
    dueno_telefono: str = ""        # a quién avisar cuando se deriva un chat (y quién puede dar comandos)

    # --- IA ---
    proveedor_ia: str = "anthropic"  # anthropic | openai | falso
    anthropic_api_key: str = ""
    anthropic_modelo: str = "claude-haiku-5-5"
    anthropic_url: str = "https://api.anthropic.com"
    openai_api_key: str = ""
    openai_modelo: str = "gpt-4.1-mini"
    openai_url: str = "https://api.openai.com"
    max_tokens_respuesta: int = 300  # tope de tokens de cada respuesta (respuestas cortas)
    tope_tokens_dia: int = 200_000   # tope de tokens (entrada + salida) por día para todo el bot. 0 = sin tope
    timeout_ia: float = 25

    # --- Negocio y conversación ---
    negocio: Path = field(default_factory=lambda: RAIZ / "negocio.md")
    base_datos: Path = field(default_factory=lambda: RAIZ / "datos" / "conversaciones.db")
    historial_mensajes: int = 10     # últimos N mensajes que se le pasan a la IA
    max_mensajes_hora: int = 20      # mensajes por cliente por hora; después el bot deja de contestar
    horas_pausa: float = 12          # horas que el bot queda pausado en un chat derivado a una persona
    zona_horaria: str = "America/Argentina/Buenos_Aires"
    horario: str = "09:00-18:00"     # horario de atención humana
    dias_habiles: str = "0-4"        # 0 = lunes ... 6 = domingo. Ej: "0-4" o "0-5"

    # --- Panel ---
    admin_clave: str = ""            # clave del panel /admin (vacía = panel deshabilitado)
    modo_demo: bool = False          # habilita POST /demo/chat para probar sin WhatsApp


def _entero(nombre: str, defecto: int) -> int:
    valor = os.environ.get(nombre, "").strip()
    try:
        return int(valor) if valor else defecto
    except ValueError as e:
        raise ErrorConfig(f"{nombre} debe ser un número entero (vino '{valor}')") from e


def _decimal(nombre: str, defecto: float) -> float:
    valor = os.environ.get(nombre, "").strip()
    try:
        return float(valor) if valor else defecto
    except ValueError as e:
        raise ErrorConfig(f"{nombre} debe ser un número (vino '{valor}')") from e


def _ruta(nombre: str, defecto: Path) -> Path:
    """Las rutas relativas del .env se toman desde la carpeta del proyecto, no desde donde se lanza el comando."""
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        return defecto
    ruta = Path(valor)
    return ruta if ruta.is_absolute() else RAIZ / ruta


def cargar(archivo_env: Path | None = None) -> Config:
    load_dotenv(archivo_env or RAIZ / ".env")
    e = lambda nombre, defecto="": os.environ.get(nombre, defecto).strip()  # noqa: E731
    proveedor = e("PROVEEDOR_IA", "anthropic").lower()
    if proveedor not in ("anthropic", "openai", "falso"):
        raise ErrorConfig(f"PROVEEDOR_IA debe ser anthropic, openai o falso (vino '{proveedor}')")
    return Config(
        token=e("WHATSAPP_TOKEN"),
        phone_number_id=e("WHATSAPP_PHONE_NUMBER_ID"),
        verify_token=e("WHATSAPP_VERIFY_TOKEN"),
        app_secret=e("WHATSAPP_APP_SECRET"),
        api_version=e("WHATSAPP_API_VERSION", "v21.0"),
        dueno_telefono=e("DUENO_TELEFONO"),
        proveedor_ia=proveedor,
        anthropic_api_key=e("ANTHROPIC_API_KEY"),
        anthropic_modelo=e("ANTHROPIC_MODELO", "claude-haiku-5-5"),
        anthropic_url=e("ANTHROPIC_URL", "https://api.anthropic.com"),
        openai_api_key=e("OPENAI_API_KEY"),
        openai_modelo=e("OPENAI_MODELO", "gpt-4.1-mini"),
        openai_url=e("OPENAI_URL", "https://api.openai.com"),
        max_tokens_respuesta=_entero("MAX_TOKENS_RESPUESTA", 300),
        tope_tokens_dia=_entero("TOPE_TOKENS_DIA", 200_000),
        timeout_ia=_decimal("TIMEOUT_IA", 25),
        negocio=_ruta("NEGOCIO", RAIZ / "negocio.md"),
        base_datos=_ruta("BASE_DATOS", RAIZ / "datos" / "conversaciones.db"),
        historial_mensajes=_entero("HISTORIAL_MENSAJES", 10),
        max_mensajes_hora=_entero("MAX_MENSAJES_HORA", 20),
        horas_pausa=_decimal("HORAS_PAUSA", 12),
        zona_horaria=e("ZONA_HORARIA", "America/Argentina/Buenos_Aires"),
        horario=e("HORARIO", "09:00-18:00"),
        dias_habiles=e("DIAS_HABILES", "0-4"),
        admin_clave=e("ADMIN_CLAVE"),
        modo_demo=e("MODO_DEMO", "0").lower() in ("1", "true", "si", "sí"),
    )
