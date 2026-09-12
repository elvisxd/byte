"""Registro, login y JWT (Fase 4).

Lo que se prueba acá es lo que distingue una autenticación que sirve de una que
parece servir: que la contraseña no se guarde en claro, que el login no diga si
un email existe, y que un token que no debería valer no valga.
"""

import time
from collections.abc import Callable

import jwt as pyjwt
import pytest
from starlette.testclient import TestClient

from api.auth import JWTService, Passwords
from tests.conftest import API_KEY, AUTH

REGISTRO = {"email": "ana@ejemplo.com", "password": "una-contraseña-larga"}


def _registrar(cliente: TestClient, **cambios: object) -> object:
    return cliente.post("/api/v1/auth/register", json={**REGISTRO, **cambios})


def _login(cliente: TestClient, **cambios: object) -> object:
    return cliente.post("/api/v1/auth/login", json={**REGISTRO, **cambios})


def _token(cliente: TestClient) -> str:
    _registrar(cliente)
    return _login(cliente).json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Registro ---


def test_registrarse_y_entrar(cliente: TestClient) -> None:
    creado = _registrar(cliente)
    assert creado.status_code == 201
    assert creado.json()["email"] == "ana@ejemplo.com"

    entrada = _login(cliente)
    assert entrada.status_code == 200
    cuerpo = entrada.json()
    assert cuerpo["token_type"] == "bearer"
    assert cuerpo["expires_in"] > 0
    assert cuerpo["access_token"]


def test_la_contrasena_nunca_vuelve(cliente: TestClient) -> None:
    """Ni en el registro ni en `/me`: el hash no sale de `db/`."""
    creado = _registrar(cliente)
    assert "password" not in creado.text
    assert "argon2" not in creado.text

    yo = cliente.get("/api/v1/me", headers=_bearer(_login(cliente).json()["access_token"]))
    assert "password" not in yo.text


def test_el_email_no_se_puede_repetir(cliente: TestClient) -> None:
    _registrar(cliente)
    repetido = _registrar(cliente, password="otra-contraseña-larga")
    assert repetido.status_code == 409
    assert repetido.json()["error"]["code"] == "conflict"


def test_el_email_no_distingue_mayusculas(cliente: TestClient) -> None:
    """`Ana@x.com` y `ana@x.com` son la misma persona: si no, dos cuentas para
    el mismo correo y el login depende de cómo lo escribas."""
    _registrar(cliente)
    assert _registrar(cliente, email="ANA@Ejemplo.com").status_code == 409
    assert _login(cliente, email="ANA@Ejemplo.com").status_code == 200


def test_una_contrasena_corta_se_rechaza(cliente: TestClient) -> None:
    assert _registrar(cliente, password="corta").status_code == 422


def test_un_email_que_no_es_email_se_rechaza(cliente: TestClient) -> None:
    assert _registrar(cliente, email="no-es-un-email").status_code == 422


def test_una_contrasena_enorme_se_rechaza(cliente: TestClient) -> None:
    """argon2 hashea lo que le den: sin tope, un cuerpo grande es un DoS de CPU
    por request."""
    assert _registrar(cliente, password="x" * 10_000).status_code == 422


# --- Login ---


def test_la_contrasena_incorrecta_no_entra(cliente: TestClient) -> None:
    _registrar(cliente)
    assert _login(cliente, password="no-es-la-contraseña").status_code == 401


def test_el_login_no_dice_si_el_email_existe(cliente: TestClient) -> None:
    """Un mensaje distinto para "no existe" y "contraseña incorrecta" convierte
    a `/auth/login` en un oráculo para enumerar usuarios."""
    _registrar(cliente)
    incorrecta = _login(cliente, password="no-es-la-contraseña")
    inexistente = _login(cliente, email="nadie@ejemplo.com", password="cualquiera")

    assert incorrecta.status_code == inexistente.status_code == 401
    assert incorrecta.json()["error"] == {
        **inexistente.json()["error"],
        "request_id": incorrecta.json()["error"]["request_id"],
    }


def test_el_login_tarda_lo_mismo_exista_o_no_el_email(cliente: TestClient) -> None:
    """El mensaje igual no alcanza si el tiempo delata: sin verificar contra un
    hash de descarte, un email desconocido responde mucho más rápido."""
    _registrar(cliente)

    def medir(**cambios: object) -> float:
        arranque = time.perf_counter()
        _login(cliente, **cambios)
        return time.perf_counter() - arranque

    con_email = min(medir(password="no-es-la-contraseña") for _ in range(3))
    sin_email = min(medir(email="nadie@ejemplo.com", password="cualquiera") for _ in range(3))

    # Un orden de magnitud sería delator; acá deberían ser comparables.
    assert 0.2 < sin_email / con_email < 5, f"con={con_email:.3f}s sin={sin_email:.3f}s"


# --- El token ---


def test_el_jwt_sirve_para_el_resto_de_la_api(cliente: TestClient) -> None:
    """El contrato dice que los endpoints no cambian: un JWT es otra forma de
    llegar, no otra API."""
    assert cliente.get("/api/v1/conversations", headers=_bearer(_token(cliente))).status_code == 200


def test_me_dice_quien_soy(cliente: TestClient) -> None:
    yo = cliente.get("/api/v1/me", headers=_bearer(_token(cliente)))
    assert yo.status_code == 200
    assert yo.json()["email"] == "ana@ejemplo.com"


def test_la_api_key_no_es_un_usuario(cliente: TestClient) -> None:
    """`/me` con una API key no tendría qué responder: identifica a la
    instancia, no a alguien."""
    assert cliente.get("/api/v1/me", headers=AUTH).status_code == 401


def test_sin_token_no_hay_me(cliente: TestClient) -> None:
    assert cliente.get("/api/v1/me").status_code == 401
    assert cliente.get("/api/v1/me", headers=_bearer("no-es-un-token")).status_code == 401


def test_la_api_key_sigue_valiendo_en_el_mismo_header(cliente: TestClient) -> None:
    """El Bearer lleva dos cosas: primero se prueba el JWT, después la clave.
    Romper esto dejaría afuera a los clientes de OpenAI y a n8n."""
    assert cliente.get("/api/v1/tools", headers=_bearer(API_KEY)).status_code == 200


# --- El JWT por dentro ---


def test_un_token_firmado_con_otro_secreto_no_vale() -> None:
    servicio = JWTService("s" * 32, 1800)
    ajeno = JWTService("otro-secreto-distinto", 1800).issue("alguien")
    assert servicio.verify(ajeno) is None


def test_un_token_sin_firma_no_vale() -> None:
    """`alg: none` es el ataque clásico contra JWT: sin `algorithms` explícito,
    un token sin firmar pasaría."""
    servicio = JWTService("s" * 32, 1800)
    sin_firma = pyjwt.encode({"sub": "admin", "exp": 9_999_999_999}, "", algorithm="none")
    assert servicio.verify(sin_firma) is None


def test_un_token_sin_expiracion_no_vale() -> None:
    """Sin `exp` requerido, un token robado valdría para siempre."""
    servicio = JWTService("s" * 32, 1800)
    eterno = pyjwt.encode({"sub": "admin"}, "s" * 32, algorithm="HS256")
    assert servicio.verify(eterno) is None


def test_un_token_vencido_no_vale() -> None:
    vencido = JWTService("s" * 32, -1).issue("alguien")
    assert JWTService("s" * 32, 1800).verify(vencido) is None


# --- Las contraseñas ---


def test_la_contrasena_se_guarda_hasheada_con_argon2() -> None:
    passwords = Passwords()
    hash_ = passwords.hash("una-contraseña-larga")

    assert hash_.startswith("$argon2id$")
    assert "una-contraseña-larga" not in hash_
    assert passwords.verify(hash_, "una-contraseña-larga")
    assert not passwords.verify(hash_, "otra-cosa")


def test_dos_veces_la_misma_contrasena_da_hashes_distintos() -> None:
    """argon2 sala cada hash: sin eso, dos usuarios con la misma contraseña se
    reconocen en la base de un vistazo."""
    passwords = Passwords()
    assert passwords.hash("la-misma-contraseña") != passwords.hash("la-misma-contraseña")


def test_un_hash_invalido_no_explota() -> None:
    """Un valor corrupto en la base tiene que ser un login fallido, no un 500."""
    assert not Passwords().verify("no-es-un-hash", "cualquier-cosa")


@pytest.mark.parametrize("valor", ["", "$argon2id$roto", "x" * 200])
def test_ningun_hash_raro_rompe_el_login(valor: str) -> None:
    assert not Passwords().verify(valor, "cualquier-cosa")


# --- Aislamiento entre usuarios ---
#
# Acá es donde el multi-usuario se vuelve real: hasta ahora el filtro existía
# pero todos los call sites pasaban `SIN_USUARIO`. Estos prueban la API entera,
# no el Repository: que la ruta le pase el dueño correcto es tan necesario como
# que el filtro exista.

OTRO = {"email": "beto@ejemplo.com", "password": "otra-contraseña-larga"}


def _dos_usuarios(cliente: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    ana = _bearer(_token(cliente))
    cliente.post("/api/v1/auth/register", json=OTRO)
    beto = _bearer(cliente.post("/api/v1/auth/login", json=OTRO).json()["access_token"])
    return ana, beto


def test_no_se_ve_la_conversacion_de_otro(cliente: TestClient) -> None:
    """404 y no 403: un 403 confirmaría que ese id existe, que es lo que un
    IDOR necesita para enumerar."""
    ana, beto = _dos_usuarios(cliente)
    suya = cliente.post("/api/v1/conversations", json={"title": "privada"}, headers=ana).json()

    assert cliente.get(f"/api/v1/conversations/{suya['id']}", headers=ana).status_code == 200
    assert cliente.get(f"/api/v1/conversations/{suya['id']}", headers=beto).status_code == 404


def test_no_se_borra_ni_se_renombra_la_conversacion_de_otro(cliente: TestClient) -> None:
    ana, beto = _dos_usuarios(cliente)
    suya = cliente.post("/api/v1/conversations", json={"title": "privada"}, headers=ana).json()

    assert cliente.delete(f"/api/v1/conversations/{suya['id']}", headers=beto).status_code == 404
    assert (
        cliente.patch(
            f"/api/v1/conversations/{suya['id']}", json={"title": "secuestrada"}, headers=beto
        ).status_code
        == 404
    )
    # Y sigue intacta para su dueña.
    intacta = cliente.get(f"/api/v1/conversations/{suya['id']}", headers=ana).json()
    assert intacta["title"] == "privada"


def test_el_listado_no_mezcla_usuarios(cliente: TestClient) -> None:
    ana, beto = _dos_usuarios(cliente)
    cliente.post("/api/v1/conversations", json={"title": "de ana"}, headers=ana)
    cliente.post("/api/v1/conversations", json={"title": "de beto"}, headers=beto)

    def titulos(headers: dict[str, str]) -> list[str]:
        return [
            c["title"]
            for c in cliente.get("/api/v1/conversations", headers=headers).json()["items"]
        ]

    assert titulos(ana) == ["de ana"]
    assert titulos(beto) == ["de beto"]


def test_no_se_escribe_en_la_conversacion_de_otro(cliente: TestClient) -> None:
    """Mandar un mensaje a una conversación ajena la haría responder con el
    historial de otro en el contexto."""
    ana, beto = _dos_usuarios(cliente)
    suya = cliente.post("/api/v1/conversations", json={"title": "privada"}, headers=ana).json()

    intruso = cliente.post(
        f"/api/v1/conversations/{suya['id']}/messages", json={"content": "hola"}, headers=beto
    )
    assert intruso.status_code == 404


def test_no_se_lee_el_mensaje_de_otro_por_su_id(cliente: TestClient) -> None:
    """`GET /messages/{id}` toma un id suelto: es el IDOR más directo."""
    ana, beto = _dos_usuarios(cliente)
    suya = cliente.post("/api/v1/conversations", json={"title": "privada"}, headers=ana).json()
    enviado = cliente.post(
        f"/api/v1/conversations/{suya['id']}/messages?wait=true",
        json={"content": "algo privado"},
        headers=ana,
    )
    assert enviado.status_code == 200
    mensaje_id = enviado.json()["message"]["id"]

    assert cliente.get(f"/api/v1/messages/{mensaje_id}", headers=ana).status_code == 200
    assert cliente.get(f"/api/v1/messages/{mensaje_id}", headers=beto).status_code == 404


def test_la_api_key_no_ve_lo_de_los_usuarios(cliente: TestClient) -> None:
    """La API key identifica a la instancia, no a alguien: su `user_id` es
    `None`, el mismo `SIN_USUARIO` de antes del multi-usuario. Lo que había
    antes sigue siendo suyo; lo de un usuario con JWT, no."""
    ana = _bearer(_token(cliente))
    cliente.post("/api/v1/conversations", json={"title": "de ana"}, headers=ana)
    cliente.post("/api/v1/conversations", json={"title": "de la instancia"}, headers=AUTH)

    con_clave = [
        c["title"] for c in cliente.get("/api/v1/conversations", headers=AUTH).json()["items"]
    ]
    assert con_clave == ["de la instancia"]


# --- Refresh token ---


def _sesion(cliente: TestClient) -> dict:
    _registrar(cliente)
    return _login(cliente).json()


def test_el_login_devuelve_un_refresh(cliente: TestClient) -> None:
    assert _sesion(cliente)["refresh_token"]


def test_el_refresh_da_un_access_nuevo_sin_la_contrasena(cliente: TestClient) -> None:
    """Es el punto: 30 minutos de access no se sostienen si hay que volver a
    escribir la contraseña cada vez."""
    inicial = _sesion(cliente)
    renovado = cliente.post(
        "/api/v1/auth/refresh", json={"refresh_token": inicial["refresh_token"]}
    )
    assert renovado.status_code == 200
    nuevo = renovado.json()["access_token"]
    assert cliente.get("/api/v1/me", headers=_bearer(nuevo)).status_code == 200


def test_el_refresh_se_rota(cliente: TestClient) -> None:
    """El token que se manda deja de valer y vuelve otro: sin rotación, uno
    robado sirve durante todo su TTL sin que nadie lo note."""
    inicial = _sesion(cliente)["refresh_token"]
    devuelto = cliente.post("/api/v1/auth/refresh", json={"refresh_token": inicial}).json()
    assert devuelto["refresh_token"] != inicial


def test_un_refresh_reusado_corta_la_familia(cliente: TestClient) -> None:
    """Un token ya canjeado que reaparece significa que hay dos copias —la del
    ladrón y la del dueño, sin forma de saber cuál— así que caen las dos.

    Es molesto a propósito: la alternativa es dejar viva la sesión robada.
    """
    primero = _sesion(cliente)["refresh_token"]
    segundo = cliente.post("/api/v1/auth/refresh", json={"refresh_token": primero}).json()[
        "refresh_token"
    ]

    # El viejo vuelve a aparecer: se corta todo.
    assert cliente.post("/api/v1/auth/refresh", json={"refresh_token": primero}).status_code == 401
    # Y el que era válido cae con la familia.
    assert cliente.post("/api/v1/auth/refresh", json={"refresh_token": segundo}).status_code == 401


def test_un_refresh_inventado_no_vale(cliente: TestClient) -> None:
    assert (
        cliente.post("/api/v1/auth/refresh", json={"refresh_token": "no-existe"}).status_code == 401
    )


def test_el_logout_cierra_la_sesion(cliente: TestClient) -> None:
    inicial = _sesion(cliente)["refresh_token"]
    assert cliente.post("/api/v1/auth/logout", json={"refresh_token": inicial}).status_code == 204
    assert cliente.post("/api/v1/auth/refresh", json={"refresh_token": inicial}).status_code == 401


def test_el_logout_no_dice_si_el_token_existia(cliente: TestClient) -> None:
    """Responder distinto lo convertiría en un oráculo de tokens válidos."""
    inventado = cliente.post("/api/v1/auth/logout", json={"refresh_token": "no-existe"})
    assert inventado.status_code == 204


def test_cerrar_sesion_no_parece_un_robo(cliente: TestClient, caplog) -> None:
    """El logout no canjea el token, solo revoca su familia: marcarlo como
    usado haría que el siguiente intento se loguee como reuso, y cerrar sesión
    no es un incidente de seguridad."""
    inicial = _sesion(cliente)["refresh_token"]
    cliente.post("/api/v1/auth/logout", json={"refresh_token": inicial})
    caplog.clear()
    cliente.post("/api/v1/auth/refresh", json={"refresh_token": inicial})
    assert "refresh_token_reusado" not in caplog.text


def test_dos_sesiones_son_independientes(cliente: TestClient) -> None:
    """Cerrar sesión en un dispositivo no debería echar al otro: cada login
    abre su propia familia."""
    _registrar(cliente)
    una = _login(cliente).json()["refresh_token"]
    otra = _login(cliente).json()["refresh_token"]

    cliente.post("/api/v1/auth/logout", json={"refresh_token": una})
    assert cliente.post("/api/v1/auth/refresh", json={"refresh_token": otra}).status_code == 200


def test_el_refresh_no_se_guarda_en_claro() -> None:
    """Si alguien lee la tabla —un backup, un dump— no debería llevarse
    credenciales usables, igual que con las contraseñas."""
    import hashlib

    from api.auth import RefreshService

    token = "un-token-cualquiera"  # noqa: S105
    assert RefreshService._hash(token) == hashlib.sha256(token.encode()).hexdigest()
    assert token not in RefreshService._hash(token)


# --- Lo que salió de la revisión adversarial de la fase ---


def test_mandar_las_dos_credenciales_no_desacopla_el_dueno(cliente: TestClient) -> None:
    """Un cliente con la API key configurada que además inicia sesión manda las
    dos cabeceras. Si la clave ganara, la conversación quedaría a nombre del
    usuario y el run a nombre de la instancia: cualquiera con la clave podría
    ver y **cancelar** el run de esa persona, mientras ella recibe 404 en el
    suyo. Verificado que pasaba antes de darle prioridad al JWT.
    """
    _registrar(cliente)
    token = _login(cliente).json()["access_token"]
    ambas = {**_bearer(token), **AUTH}

    conversacion = cliente.post("/api/v1/conversations", json={}, headers=ambas).json()
    run_id = cliente.post(
        f"/api/v1/conversations/{conversacion['id']}/messages",
        json={"content": "hola"},
        headers=ambas,
    ).json()["run_id"]

    # El run es del usuario, no de la instancia.
    assert cliente.get(f"/api/v1/runs/{run_id}", headers=_bearer(token)).status_code == 200
    assert cliente.get(f"/api/v1/runs/{run_id}", headers=AUTH).status_code == 404
    assert cliente.post(f"/api/v1/runs/{run_id}/cancel", headers=AUTH).status_code == 404


def test_el_token_de_un_usuario_borrado_deja_de_valer(cliente: TestClient) -> None:
    """Un JWT sigue siendo criptográficamente válido sus 30 minutos aunque su
    usuario ya no exista. Sin verificarlo contra la base, borrar a alguien no
    tenía efecto hasta que venciera: en memoria seguía leyendo y escribiendo, y
    en Postgres escribir daba un 500 por la FK."""
    _registrar(cliente)
    token = _login(cliente).json()["access_token"]
    H = _bearer(token)
    assert cliente.get("/api/v1/conversations", headers=H).status_code == 200

    ctx = cliente.app.state.ctx
    ctx.repository._usuarios.clear()

    assert cliente.get("/api/v1/conversations", headers=H).status_code == 401
    assert cliente.post("/api/v1/conversations", json={}, headers=H).status_code == 401


def test_un_sub_que_no_es_uuid_da_401_y_no_500(cliente: TestClient) -> None:
    """El `sub` iba crudo al SQL: un uuid inválido volvía como error interno en
    vez de como credencial rechazada. Hace falta el secreto para forjarlo, así
    que no era un bypass — pero un 500 es ruido y esconde lo que pasó."""
    from datetime import UTC, datetime, timedelta

    falso = pyjwt.encode(
        {"sub": "no-soy-un-uuid", "exp": datetime.now(UTC) + timedelta(hours=1)},
        "s" * 32,
        algorithm="HS256",
    )
    assert cliente.get("/api/v1/conversations", headers=_bearer(falso)).status_code == 401


def test_el_limite_del_login_no_se_multiplica_por_credencial(
    crear_cliente: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """En el login, quien ataca **sí tiene** credenciales válidas: la API key, o
    una cuenta propia recién registrada. Limitando por credencial, cada
    identidad le daba una cubeta nueva contra la misma víctima desde la misma
    IP — y con el registro abierto eso no tiene techo."""
    monkeypatch.setenv("BYTE_RATE_LIMIT_RUNS", "3/minute")
    cliente = crear_cliente()
    cliente.post("/api/v1/auth/register", json=REGISTRO)

    def intentar(headers: dict[str, str] | None = None) -> int:
        return cliente.post(
            "/api/v1/auth/login",
            json={**REGISTRO, "password": "incorrecta-pero-larga"},
            headers=headers or {},
        ).status_code

    codigos = [intentar() for _ in range(6)]
    assert 429 in codigos, f"el límite no se aplicó: {codigos}"
    # Y con una credencial válida no se reinicia la cubeta.
    assert intentar(AUTH) == 429


def test_los_nonces_gastados_tienen_tope(cliente: TestClient) -> None:
    """La poda era solo por tiempo, así que el dict crecía sin cota durante una
    hora entera y cada run emite uno."""
    import time

    from api.security import MAX_NONCES_RETENIDOS, TokenService

    tokens = TokenService("s" * 32, 60, 86400, 3600)
    for i in range(MAX_NONCES_RETENIDOS + 500):
        tokens._spent[f"nonce-{i}"] = time.monotonic()
    tokens._prune_spent()

    assert len(tokens._spent) == MAX_NONCES_RETENIDOS
