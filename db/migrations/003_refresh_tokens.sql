-- Byte — refresh tokens (Fase 4).
--
-- El access token vive 30 minutos y no se revoca: si se roba, vale hasta que
-- expira. El refresh vive días, así que robarlo es mucho peor — y por eso este
-- sí se guarda, se rota en cada uso y se puede revocar.
--
-- Se guarda **el hash**, no el token. Si alguien lee esta tabla (un backup, un
-- dump, un `SELECT *` de más) no se lleva credenciales usables, igual que con
-- `users.password_hash`. Acá alcanza SHA-256: a diferencia de una contraseña, el
-- token es aleatorio de 256 bits y no hay diccionario que probar.

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash text UNIQUE NOT NULL,
    -- Familia: todos los tokens que descienden de un mismo login. La rotación
    -- emite uno nuevo por cada uso, y si aparece un token ya canjeado se revoca
    -- la familia entera (ver `revoked_at`).
    family_id  uuid NOT NULL,
    expires_at timestamptz NOT NULL,
    -- Cuándo se canjeó. Un token usado no vale más; que vuelva a aparecer es la
    -- señal de que alguien tiene una copia.
    used_at    timestamptz,
    -- Cuándo se revocó y por qué. Se conserva la fila en vez de borrarla: sin
    -- eso, un token reusado sería indistinguible de uno que nunca existió.
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- El login lista y revoca por familia; la limpieza barre por expiración.
CREATE INDEX IF NOT EXISTS refresh_tokens_family_idx ON refresh_tokens (family_id);
CREATE INDEX IF NOT EXISTS refresh_tokens_expires_idx ON refresh_tokens (expires_at);
