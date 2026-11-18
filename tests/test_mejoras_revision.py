"""Pruebas agregadas en la revisión: la IA falsa usa los títulos de sección y el arranque sin clave es claro."""
import pytest

from bot import app as modulo_app
from bot.ia import ProveedorFalso

SISTEMA = """<negocio>
## Horarios
- Abrimos de martes a domingo de 19:00 a 00:00. Los lunes cerramos.
## Envíos
- El envío cuesta $1.500.
</negocio>"""


def preguntar(texto: str) -> str:
    return ProveedorFalso().responder(SISTEMA, [{"role": "user", "content": texto}], 300).texto


def test_pregunta_por_el_titulo_de_la_seccion():
    assert "19:00" in preguntar("Hola, ¿qué horarios tienen?")


def test_la_coincidencia_en_la_linea_gana_al_titulo():
    assert "$1.500" in preguntar("¿cuánto cuesta el envío?")


def test_arranque_sin_clave_termina_con_mensaje_claro(monkeypatch, caplog):
    monkeypatch.setenv("PROVEEDOR_IA", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(modulo_app, "crear_app", lambda: (_ for _ in ()).throw(modulo_app.ErrorIA("Falta ANTHROPIC_API_KEY")))
    with pytest.raises(SystemExit):
        modulo_app.crear_app_por_defecto()
    assert "Completá el .env" in caplog.text
