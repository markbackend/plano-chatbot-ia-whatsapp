"""Lógica del bot: IA, base de conocimiento, derivación, pausa, límites y respaldo."""

from datetime import timedelta

from conftest import CLIENTE, DOMINGO_23, DUENO, MIERCOLES_11, IAFija, entrante

from bot.conversacion import BOTON_PERSONA, pide_persona
from bot.ia import MARCA_DERIVAR, ProveedorFalso
from bot.idioma import detectar_idioma


def test_responde_con_datos_del_negocio(crear_bot):
    bot = crear_bot()
    salidas = bot.procesar(entrante("¿Cuánto sale la pizza muzzarella?"), MIERCOLES_11)
    assert len(salidas) == 1 and salidas[0].para == CLIENTE
    assert "$12.500" in salidas[0].texto


def test_el_prompt_incluye_el_negocio_y_las_reglas(crear_bot):
    ia = IAFija()
    crear_bot(ia).procesar(entrante("hola"), MIERCOLES_11)
    sistema = ia.llamadas[0]["sistema"]
    assert "Pizza muzzarella grande: $12.500" in sistema
    assert "No inventes" in sistema and MARCA_DERIVAR in sistema and "idioma" in sistema
    assert ia.llamadas[0]["max_tokens"] == 300


def test_si_la_ia_no_sabe_deriva_pausa_y_avisa_al_dueno(crear_bot, almacen):
    bot = crear_bot()
    salidas = bot.procesar(entrante("¿Tienen sushi?"), MIERCOLES_11)
    assert [s.para for s in salidas] == [CLIENTE, DUENO]
    assert MARCA_DERIVAR not in salidas[0].texto
    assert "#reactivar " + CLIENTE in salidas[1].texto and "sushi" in salidas[1].texto
    assert almacen.pausada(CLIENTE, MIERCOLES_11)
    # Mientras está pausado, el bot no contesta ni llama a la IA
    assert bot.procesar(entrante("¿hola?"), MIERCOLES_11 + timedelta(minutes=5)) == []
    # Y se reactiva solo después de HORAS_PAUSA
    assert not almacen.pausada(CLIENTE, MIERCOLES_11 + timedelta(hours=13))


def test_cliente_pide_persona_por_texto_o_boton(crear_bot, almacen):
    ia = IAFija()
    bot = crear_bot(ia)
    salidas = bot.procesar(entrante("quiero hablar con una persona"), MIERCOLES_11)
    assert "persona del equipo" in salidas[0].texto and salidas[1].para == DUENO
    assert ia.llamadas == []  # no gasta IA
    salidas = bot.procesar(entrante("Hablar con persona", telefono="5491177778888", boton=BOTON_PERSONA), MIERCOLES_11)
    assert almacen.pausada("5491177778888", MIERCOLES_11) and len(salidas) == 2


def test_deteccion_de_pedido_de_persona():
    for texto in ("Quiero hablar con un asesor", "pasame con alguien", "humano", "I want to talk to a human",
                  "Quero falar com uma pessoa", "Can I speak with an agent?"):
        assert pide_persona(texto), texto
    for texto in ("Una pizza para cuatro personas", "hola", "¿hacen envíos?"):
        assert not pide_persona(texto), texto


def test_fuera_de_horario_lo_dice(crear_bot):
    salidas = crear_bot(IAFija()).procesar(entrante("quiero hablar con una persona"), DOMINGO_23)
    assert "fuera de horario" in salidas[0].texto and "09:00-18:00" in salidas[0].texto


def test_dueno_reactiva_con_comando(crear_bot, almacen):
    bot = crear_bot()
    bot.procesar(entrante("¿Tienen sushi?"), MIERCOLES_11)
    assert almacen.pausada(CLIENTE, MIERCOLES_11)
    r = bot.procesar(entrante("#pausados", telefono=DUENO), MIERCOLES_11)
    assert CLIENTE in r[0].texto
    r = bot.procesar(entrante(f"#reactivar +{CLIENTE}", telefono=DUENO), MIERCOLES_11)
    assert "vuelve a contestar" in r[0].texto
    assert not almacen.pausada(CLIENTE, MIERCOLES_11)
    assert bot.procesar(entrante("¿Hacen delivery?"), MIERCOLES_11)[0].texto.startswith("Hacemos delivery")


def test_comandos_del_dueno_errores_y_pausa_manual(crear_bot, almacen):
    bot = crear_bot()
    assert "No encontré" in bot.procesar(entrante("#reactivar 123", telefono=DUENO), MIERCOLES_11)[0].texto
    assert "Comandos" in bot.procesar(entrante("#ayuda", telefono=DUENO), MIERCOLES_11)[0].texto
    bot.procesar(entrante("hola"), MIERCOLES_11)
    assert "2 h" in bot.procesar(entrante(f"#pausar {CLIENTE} 2", telefono=DUENO), MIERCOLES_11)[0].texto
    assert almacen.pausada(CLIENTE, MIERCOLES_11 + timedelta(hours=1))
    assert not almacen.pausada(CLIENTE, MIERCOLES_11 + timedelta(hours=3))


def test_un_cliente_no_puede_usar_comandos(crear_bot, almacen):
    bot = crear_bot(IAFija("No entiendo"))
    bot.procesar(entrante("¿Tienen sushi?", telefono="5491155556666"), MIERCOLES_11)
    salidas = bot.procesar(entrante("#reactivar 5491155556666", telefono=CLIENTE), MIERCOLES_11)
    assert salidas[0].texto == "No entiendo"  # lo trató como mensaje normal


def test_limite_de_mensajes_por_hora(crear_bot):
    ia = IAFija()
    bot = crear_bot(ia, max_mensajes_hora=3)
    for _ in range(3):
        assert bot.procesar(entrante("hola"), MIERCOLES_11)[0].texto == ia.texto
    aviso = bot.procesar(entrante("hola"), MIERCOLES_11)
    assert "muchos mensajes" in aviso[0].texto
    assert bot.procesar(entrante("hola"), MIERCOLES_11) == []  # el aviso sale una sola vez
    assert len(ia.llamadas) == 3
    # Una hora después vuelve a contestar
    assert bot.procesar(entrante("hola"), MIERCOLES_11 + timedelta(hours=1, minutes=1))[0].texto == ia.texto


def test_respuesta_de_respaldo_si_la_ia_falla(crear_bot):
    salidas = crear_bot(ProveedorFalso(falla=True)).procesar(entrante("hola, ¿precio?"), MIERCOLES_11)
    assert "no puedo responder" in salidas[0].texto
    assert salidas[0].botones == ((BOTON_PERSONA, "Hablar con persona"),)


def test_respaldo_si_la_ia_lanza_un_error_inesperado(crear_bot):
    class IARota:
        nombre = "rota"

        def responder(self, *a):
            raise KeyError("algo raro")
    assert "no puedo responder" in crear_bot(IARota()).procesar(entrante("hola"), MIERCOLES_11)[0].texto


def test_tope_diario_de_tokens(crear_bot, almacen):
    ia = IAFija(tokens=600)
    bot = crear_bot(ia, tope_tokens_dia=1000)
    bot.procesar(entrante("uno"), MIERCOLES_11)
    bot.procesar(entrante("dos"), MIERCOLES_11)
    assert almacen.tokens_del_dia("2026-11-18") == 1200
    salidas = bot.procesar(entrante("tres"), MIERCOLES_11)
    assert "no puedo responder" in salidas[0].texto and len(ia.llamadas) == 2
    # Al día siguiente el contador arranca de cero
    assert bot.procesar(entrante("cuatro"), MIERCOLES_11 + timedelta(days=1))[0].texto == ia.texto


def test_no_envia_precios_que_no_estan_en_el_negocio(crear_bot, almacen):
    salidas = crear_bot(IAFija("La pizza de rúcula sale $15.990")).procesar(entrante("¿y la de rúcula?"), MIERCOLES_11)
    assert "15.990" not in salidas[0].texto and salidas[1].para == DUENO
    assert "precio" in salidas[1].texto
    assert almacen.pausada(CLIENTE, MIERCOLES_11)


def test_precios_reales_traducidos_pasan(crear_bot):
    salidas = crear_bot(IAFija("The large muzzarella pizza costs $12,500.")).procesar(
        entrante("How much is the muzzarella pizza?"), MIERCOLES_11)
    assert salidas == [salidas[0]] and "$12,500" in salidas[0].texto


def test_historial_limitado_a_los_ultimos_n(crear_bot):
    ia = IAFija("ok")
    bot = crear_bot(ia, historial_mensajes=4)
    for i in range(5):
        bot.procesar(entrante(f"mensaje {i}"), MIERCOLES_11 + timedelta(minutes=i))
    ultimo = ia.llamadas[-1]["mensajes"]
    assert len(ultimo) == 4
    assert ultimo[-1] == {"role": "user", "content": "mensaje 4"}
    assert ultimo[0] == {"role": "assistant", "content": "ok"}


def test_mensaje_repetido_se_ignora(crear_bot):
    ia = IAFija()
    bot = crear_bot(ia)
    assert bot.procesar(entrante("hola", id="wamid.X"), MIERCOLES_11)
    assert bot.procesar(entrante("hola", id="wamid.X"), MIERCOLES_11) == []
    assert len(ia.llamadas) == 1


def test_audio_o_imagen_pide_texto(crear_bot):
    ia = IAFija()
    salidas = crear_bot(ia).procesar(entrante("[audio]", tipo="audio"), MIERCOLES_11)
    assert "texto" in salidas[0].texto and ia.llamadas == []


def test_mensajes_fijos_en_el_idioma_del_cliente(crear_bot):
    bot = crear_bot(ProveedorFalso(falla=True))
    assert "Sorry" in bot.procesar(entrante("Hello, how much is it?", telefono="1"), MIERCOLES_11)[0].texto
    assert "Desculpe" in bot.procesar(entrante("Olá, quanto custa?", telefono="2"), MIERCOLES_11)[0].texto
    en = bot.procesar(entrante("I want to talk to a human", telefono="3"), MIERCOLES_11)
    assert en[0].texto.startswith("I'm passing you")


def test_detectar_idioma():
    assert detectar_idioma("Hola, ¿cuánto sale?") == "es"
    assert detectar_idioma("Hi, do you have delivery?") == "en"
    assert detectar_idioma("Olá, vocês fazem entrega?") == "pt"
    assert detectar_idioma("") == "es" and detectar_idioma("123") == "es"


def test_sin_dueno_configurado_igual_deriva(crear_bot, almacen):
    salidas = crear_bot(dueno_telefono="").procesar(entrante("¿Tienen sushi?"), MIERCOLES_11)
    assert len(salidas) == 1 and almacen.pausada(CLIENTE, MIERCOLES_11)
