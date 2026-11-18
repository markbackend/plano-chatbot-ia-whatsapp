"""Configuración, base de conocimiento, almacén y simulador."""

from datetime import datetime

import pytest
from conftest import CLIENTE, MIERCOLES_11

from bot import config as cfg
from bot import simulador
from bot.almacen import Almacen
from bot.conocimiento import armar_prompt, cargar_negocio, precios_en, precios_inventados


@pytest.fixture
def entorno_limpio(monkeypatch, tmp_path):
    for nombre in ("PROVEEDOR_IA", "MAX_MENSAJES_HORA", "ANTHROPIC_MODELO", "MODO_DEMO", "ANTHROPIC_API_KEY",
                   "NEGOCIO", "BASE_DATOS"):
        monkeypatch.delenv(nombre, raising=False)
    vacio = tmp_path / ".env"
    vacio.write_text("", encoding="utf-8")
    return vacio


def test_config_por_defecto(entorno_limpio):
    c = cfg.cargar(entorno_limpio)
    assert c.anthropic_modelo == "claude-haiku-5-5" and c.proveedor_ia == "anthropic"
    assert c.max_mensajes_hora == 20 and c.modo_demo is False


def test_config_desde_entorno(entorno_limpio, monkeypatch):
    monkeypatch.setenv("PROVEEDOR_IA", "OpenAI")
    monkeypatch.setenv("MAX_MENSAJES_HORA", "5")
    monkeypatch.setenv("MODO_DEMO", "1")
    monkeypatch.setenv("NEGOCIO", "clientes/otro.md")
    c = cfg.cargar(entorno_limpio)
    assert c.proveedor_ia == "openai" and c.max_mensajes_hora == 5 and c.modo_demo
    assert c.negocio == cfg.RAIZ / "clientes" / "otro.md"  # relativa a la carpeta del proyecto


def test_config_invalida(entorno_limpio, monkeypatch):
    monkeypatch.setenv("MAX_MENSAJES_HORA", "muchos")
    with pytest.raises(cfg.ErrorConfig, match="MAX_MENSAJES_HORA"):
        cfg.cargar(entorno_limpio)
    monkeypatch.setenv("MAX_MENSAJES_HORA", "5")
    monkeypatch.setenv("PROVEEDOR_IA", "gemini")
    with pytest.raises(cfg.ErrorConfig, match="PROVEEDOR_IA"):
        cfg.cargar(entorno_limpio)


def test_negocio_inexistente_no_rompe(tmp_path):
    assert cargar_negocio(tmp_path / "no-existe.md") == ""
    assert "(sin información cargada)" in armar_prompt("")


def test_deteccion_de_precios():
    assert precios_en("Sale $12.500 y el envío US$ 10") == {"12500", "10"}
    assert precios_en("costs 12,500 pesos or R$ 50,00") == {"12500", "50"}
    assert precios_en("abrimos a las 19:00") == set()
    negocio = "Pizza: $12.500\nEnvío: $1.500"
    assert precios_inventados("La pizza sale $12.500", negocio) == set()
    assert precios_inventados("La pizza sale $13.000", negocio) == {"13000"}


def test_almacen_en_archivo_persiste(tmp_path):
    ruta = tmp_path / "sub" / "bot.db"
    a = Almacen(ruta)
    a.tocar(CLIENTE, "Ana", MIERCOLES_11)
    a.guardar_mensaje("1", CLIENTE, "entrante", "hola", MIERCOLES_11)
    a.db.close()
    b = Almacen(ruta)
    assert b.historial(CLIENTE, 5) == [{"role": "user", "content": "hola"}]
    assert b.conversaciones()[0]["nombre"] == "Ana"


def test_simulador_guion_con_ia_falsa(entorno_limpio, monkeypatch):
    monkeypatch.setattr(cfg, "load_dotenv", lambda *a, **k: None)  # que no lea un .env real
    lineas = []
    assert simulador.main(["--guion", "--falsa"], escribir=lineas.append) == 0
    texto = "\n".join(lineas)
    assert "IA falsa" in texto
    assert "$12.500" in texto and "[en portugués]" in texto and "[en inglés]" in texto
    assert "Chat derivado" in texto and "vuelve a contestar" in texto
    assert "el bot no contesta" in texto


def test_simulador_interactivo(entorno_limpio, monkeypatch):
    monkeypatch.setattr(cfg, "load_dotenv", lambda *a, **k: None)
    entradas = iter(["¿Hacen delivery?", "/persona", f"/dueno #reactivar {simulador.CLIENTE}", "/cliente 549000",
                     "hola", "/salir"])
    lineas = []
    simulador.main(["--falsa"], leer=lambda _: next(entradas), escribir=lineas.append,
                   ahora=datetime(2026, 11, 18, 11, 0))
    texto = "\n".join(lineas)
    assert "Hacemos delivery" in texto and "persona del equipo" in texto
    assert "vuelve a contestar" in texto and "Ahora sos +549000" in texto and texto.endswith("Chau.")


def test_simulador_usa_ia_real_si_hay_clave(entorno_limpio, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    bot, descripcion = simulador.armar_bot(cfg.cargar(entorno_limpio), forzar_falsa=False)
    assert "Anthropic claude-haiku-5-5" in descripcion and bot.ia.nombre == "anthropic"
    _, descripcion = simulador.armar_bot(cfg.cargar(entorno_limpio), forzar_falsa=True)
    assert "falsa" in descripcion
