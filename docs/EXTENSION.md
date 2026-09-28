# Waivers Connect (extensión compañera, sin store) v0.1.1

Un botón que lee tu sesión de Sleeper (clave exacta `localStorage.token`,
comillas fuera, cookie incl. HttpOnly) y la envía a la app local, que la
**prueba contra Sleeper antes de guardar**. Cero estado propio: todo vive
en el `.env` de la app. Sin red salvo `sleeper.com` y `localhost`.

Cómo viaja el token: el popup NO habla red directa. Lee la pestaña
(`chrome.scripting`, mundo de la página) y pasa JWT+sesión al service
worker (`background.js`), que POSTea a la URL fija
`http://127.0.0.1:3001/api/auth/import`. Con `host_permissions` de
`127.0.0.1` el worker no necesita CORS; el backend además solo acepta
`Origin: chrome-extension://…` o loopback propio (ver `_check_local_caller`).

## Ensayo de aceptación (hacer una vez por versión)

1. App Waivers corriendo. Sideload fresco de esta carpeta.
2. En tu Chrome, `sleeper.com` con sesión iniciada.
3. Clic al icono → **Enviar sesión a la app**.
4. Esperado: `conectado ✓ (N ligas)` en segundos; `.env` con JWT;
   `/api/freshness` conectado; el modal de la app lo refleja tras ↻.
5. Con la app cerrada: esperado `app cerrada: abre Waivers…` (sin traceback).

## Instalar (una vez, Chrome o Edge)

1. Descarga `waivers-connect-0.1.1.zip` y descomprímelo (finirás con una
   carpeta `ext/` con `manifest.json` dentro).
2. Abre `chrome://extensions` (Edge: `edge://extensions`).
3. Activa **Developer mode** (esquina superior derecha).
4. **Load unpacked** → elige la carpeta `ext/`.
5. Fija el icono (pin) para verlo junto a la barra de direcciones.
6. Verás un aviso "unpacked extension" en cada arranque: normal, inofensivo.

Firefox: sideload temporal (se pierde al cerrar) — no soportado por ahora.

## Usar (un clic, ~una vez al año que caduca el JWT)

1. Abre la app Waivers (debe estar corriendo: el destino es tu localhost).
2. En Chrome/Edge, abre `sleeper.com` e inicia sesión.
3. Clic al icono **Waivers Connect** → **Enviar sesión a la app**.
4. `conectado ✓ (N ligas)` → vuelve a la app, pulsa ↻. Listo.
5. Si dice `app cerrada`: abre Waivers e inténtalo de nuevo.
6. Si dice `sin JWT`: esa pestaña no tiene sesión (¿ventana incógnito?
   ¿otro perfil?). Loguea en una pestaña normal.
7. **Copiar JWT (manual)**: fallback cuando prefieras pegar a mano en el
   modal conectar de la app.

## Quitarla

`chrome://extensions` → Remove. No deja nada: nunca guardó nada.

## Para desarrolladores

Fuente en `ext/` (sin build). Empaquetar:
`powershell -ExecutionPolicy Bypass -File scripts/build-ext.ps1`
→ `waivers-connect-<ver>.zip` (ignorado por git, release asset).
