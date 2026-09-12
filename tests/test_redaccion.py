"""Redacción de secretos y PII antes de salir hacia un tercero (Fase 4).

Lo que se prueba es una promesa concreta: si esto deja pasar algo, se fue a un
servicio que no controlás y no se puede deshacer. Por eso cada patrón tiene su
caso, y también los falsos positivos — redactar de más arruina la traza que
justamente se quería leer.
"""

import pytest

from api.redaccion import MAX_CHARS, mask_langfuse, redactar, redactar_dato


@pytest.mark.parametrize(
    ("texto", "marca"),
    [
        ("mi clave es sk-proj-AbCdEfGh1234567890XyZ", "[API_KEY]"),
        ("usá ghp_1234567890abcdefghijklmnopqrstuvwxyz", "[API_KEY]"),
        ("slack xoxb-1234567890-abcdefghijkl", "[API_KEY]"),
        ("aws AKIAIOSFODNN7EXAMPLE", "[API_KEY]"),
        ("google AIzaSyC1234567890abcdefghijklmnopqrstuvw", "[API_KEY]"),
        ("langfuse sk-lf-1234567890abcdefghij", "[API_KEY]"),
    ],
)
def test_las_claves_con_prefijo_conocido_se_redactan(texto: str, marca: str) -> None:
    """Son las que más daño hacen y las más fáciles de reconocer: alguien pega
    una clave para que el agente la use y queda en la traza para siempre."""
    redactado = redactar(texto)
    assert marca in redactado
    assert "1234567890" not in redactado or marca in redactado


def test_un_jwt_se_redacta_por_su_forma() -> None:
    """No hace falta saber de quién es: tres bloques base64url con puntos solo
    es un JWT. Suelto en el texto lo atrapa el patrón de JWT; detrás de un
    `Bearer` lo atrapa antes el de credencial, y las dos marcas sirven."""
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27u"
    assert redactar(f"el token era {token} y venció") == "el token era [JWT] y venció"
    assert token not in redactar(f"Authorization: Bearer {token}")


def test_un_bearer_opaco_tambien_se_redacta() -> None:
    """El formato HTTP real separa con espacio, no con `:` ni `=`. Exigir esos
    dejaba pasar entero cualquier bearer que no fuera un JWT —el `events_token`
    de Byte, un token de API de terceros— aunque el docstring prometiera
    cubrirlo. El test viejo pasaba solo porque usaba un JWT."""
    redactado = redactar("Authorization: Bearer AbCdEfGhIjKlMnOpQrStUv")
    assert "AbCdEfGhIjKlMnOpQrStUv" not in redactado
    assert "Bearer" in redactado


@pytest.mark.parametrize(
    "texto",
    [
        "access_token=abc123def456",
        "refresh_token=xyz789abc123",
        "client_secret=miclientesecreto",
        '{"password": "supersecreta123"}',
        '"api_key": "abc123def456"',
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
    ],
)
def test_los_nombres_compuestos_y_el_json_no_se_escapan(texto: str) -> None:
    """`\\btoken\\b` no matchea `access_token` porque el `_` es carácter de
    palabra, y el JSON mete comillas entre el nombre y el valor —que es justo
    el formato de los resultados de herramienta."""
    assert "[CREDENCIAL]" in redactar(texto)


def test_el_nombre_de_la_credencial_sobrevive_aunque_coincida_con_el_valor() -> None:
    """Reemplazar por contenido y no por posición se comía el nombre cuando los
    dos eran iguales, que es lo contrario de lo que se quiere."""
    assert redactar("password=password") == "password=[CREDENCIAL]"


def test_una_url_con_credenciales_se_redacta_entera() -> None:
    """El usuario y la contraseña van pegados al host: redactar solo la
    contraseña dejaría media credencial."""
    redactado = redactar("DATABASE_URL=postgresql://byte:supersecreta@localhost:5432/byte")
    assert "supersecreta" not in redactado
    assert "[DSN]" in redactado


def test_el_valor_de_una_credencial_se_redacta_pero_el_nombre_queda() -> None:
    """`Authorization: Bearer [CREDENCIAL]` sigue diciendo qué header era, que
    es lo que sirve para depurar. `[CREDENCIAL]` a secas no dice nada."""
    redactado = redactar("api_key=abc123def456ghi en el config")
    assert "abc123def456ghi" not in redactado
    assert "api_key=" in redactado


def test_los_emails_se_redactan() -> None:
    redactado = redactar("escribime a elvis@ejemplo.com o a ana@otro.org")
    assert "@ejemplo.com" not in redactado
    assert redactado.count("[EMAIL]") == 2


@pytest.mark.parametrize(
    "numero",
    ["4111 1111 1111 1111", "4111-1111-1111-1111", "5500005555555559", "378282246310005"],
)
def test_las_tarjetas_se_redactan(numero: str) -> None:
    assert redactar(f"la tarjeta {numero} venció") == "la tarjeta [TARJETA] venció"


@pytest.mark.parametrize(
    "numero",
    [
        "1234567890123456789",  # id largo, no pasa Luhn
        "20260912020000000",  # un timestamp repetido
        "1111111111111111",  # dieciséis unos
    ],
)
def test_un_numero_largo_que_no_es_tarjeta_no_se_toca(numero: str) -> None:
    """Sin validar Luhn, cualquier id largo se redactaría: la traza quedaría
    ilegible justo donde hace falta leerla."""
    assert numero in redactar(f"el pedido {numero} salió")


def test_el_texto_normal_no_se_toca() -> None:
    """Redactar de más arruina la traza que se quería leer."""
    texto = "el agente buscó en la web sobre FastAPI 0.136 y tardó 42 segundos"
    assert redactar(texto) == texto


def test_lo_que_va_al_prompt_se_redacta_aunque_venga_anidado() -> None:
    """Langfuse manda diccionarios: el input de una herramienta, su output, la
    metadata. Si solo se redactara el texto suelto, el 90% pasaría igual."""
    dato = {
        "mensajes": [{"role": "user", "content": "mi clave es sk-proj-AbCdEfGh1234567890"}],
        "metadata": {"email": "ana@ejemplo.com", "intentos": 3},
    }
    redactado = redactar_dato(dato)

    assert "[API_KEY]" in redactado["mensajes"][0]["content"]
    assert redactado["metadata"]["email"] == "[EMAIL]"
    # Lo que no es texto queda como estaba: un número no es un secreto.
    assert redactado["metadata"]["intentos"] == 3


def test_el_texto_enorme_se_recorta() -> None:
    """Una traza no necesita el documento entero, y un cuerpo enorme es una
    forma cara de filtrar datos sin darse cuenta."""
    redactado = redactar_dato("x" * (MAX_CHARS * 3))
    assert len(redactado) < MAX_CHARS + 100
    assert "recortado" in redactado


def test_una_estructura_demasiado_anidada_no_cuelga() -> None:
    """Redactar recursivamente sin tope es una forma de colgarse con datos que
    vienen de afuera."""
    hondo: dict = {}
    actual = hondo
    for _ in range(50):
        actual["mas"] = {}
        actual = actual["mas"]
    assert redactar_dato(hondo) is not None


def test_la_firma_del_mask_es_la_que_espera_langfuse() -> None:
    """Se pasa al constructor del cliente y corre sobre todo lo que el SDK está
    por mandar. Si la firma no encaja, el SDK lo ignora y todo sale en claro:
    el peor fallo posible, porque es silencioso."""
    assert mask_langfuse(data="clave sk-proj-AbCdEfGh1234567890") == "clave [API_KEY]"
    # Y acepta los kwargs extra que el SDK le pase.
    assert mask_langfuse(data="hola", trace_id="abc", cualquier_cosa=1) == "hola"


def test_un_texto_patologico_no_cuelga_el_proceso() -> None:
    """Las regex de email y DSN son cuadráticas, y el texto lo elige quien
    escribe: 62 KB de `a@a.a.a…` —que entran cómodo en el cuerpo de un POST—
    tardaban más de 3 segundos contra los 2,7 ms de prosa normal. Es CPU pura
    sin `await`, así que bloquea el event loop entero.

    El arreglo es recortar **antes** de redactar: el tope existía pero se
    aplicaba después, así que no protegía del costo.
    """
    import time

    patologico = "a@" + "a." * 31_000
    arranque = time.perf_counter()
    redactar_dato(patologico)
    tardo = time.perf_counter() - arranque

    assert tardo < 0.5, f"tardó {tardo:.2f}s: la mitigación del ReDoS no está"


def test_recortar_antes_no_parte_un_secreto_por_la_mitad() -> None:
    """Se corta con margen y no justo en `MAX_CHARS`: un secreto partido por el
    corte no lo atraparía ningún patrón."""
    relleno = "x" * (MAX_CHARS - 20)
    redactado = redactar_dato(f"{relleno} sk-proj-AbCdEfGh1234567890XyZ")
    assert "sk-proj" not in redactado
