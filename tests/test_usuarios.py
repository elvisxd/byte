"""Registro, login y JWT (Fase 4).

Lo que se prueba acá es lo que distingue una autenticación que sirve de una que
parece servir: que la contraseña no se guarde en claro, que el login no diga si
un email existe, y que un token que no debería valer no valga.
"""

import time

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
