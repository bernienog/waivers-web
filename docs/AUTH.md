# Conectar Sleeper (sin F12)

La app necesita tu sesión de Sleeper (JWT + cookie) para leer pendings y
enviar claims. Todo vive en tu máquina: `.env` + memoria del backend.
El JWT jamás sale de aquí salvo hacia `sleeper.com`.

## Ruta normal: Waivers Connect (extensión, un clic)

1. Instala la extensión una vez (docs/EXTENSION.md, ~1 minuto, sin store).
2. Abre la app Waivers (debe estar corriendo: el destino es tu localhost).
3. En tu navegador, abre `sleeper.com` e inicia sesión como siempre.
4. Clic al icono **Waivers Connect** → **Enviar sesión a la app**.
5. `conectado ✓ (N ligas)` → vuelve a la app, pulsa ↻. Listo.
6. La extensión prueba el token contra Sleeper *antes* de guardar:
   si dice `rechazado`, re-loguea en sleeper.com e inténtalo de nuevo.

## Ruta manual (pegar)

1. En la app, pulsa **conectar** en el banner `JWT …`.
2. Pulsa **abrir**, inicia sesión en sleeper.com.
3. Copia el JWT: botón **Copiar JWT** de Waivers Connect, o a mano en
   DevTools → Application → Local Storage → sleeper.com → `token`
   (valor largo con 2 puntos, `eyJ…`, sin comillas).
4. Pega en el campo 1) del modal y **guarda**. El backend valida el JWT
   *antes* de guardar y te dice `válido` / `expira` / `expired`.
5. La sesión (cookie `sleeper-web-session`) es **opcional**: prueba solo
   con el JWT primero.
6. El user id se saca solo del JWT (casi siempre). Si tras guardar tus
   ligas no cargan, pega los dígitos de tu perfil en el campo 3).

## Re-farmear

El JWT caduca (~1 año). Cuando el banner diga `JWT expira` / `expired`,
repite la ruta normal (un clic) o pega de nuevo.

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| La extensión dice `app cerrada` | Abre Waivers primero: el destino es tu localhost. |
| La extensión dice `sin JWT` | Esa pestaña no tiene sesión (¿incógnito? ¿otro perfil?). Loguea en una pestaña normal. |
| `jwt expired/invalid` al guardar | Sesión vieja: re-loguea en sleeper.com y repite. |
| `origen no permitido` | Esa llamada no vino de la app ni de la extensión: usa una de las dos rutas. |

## Por qué el login asistido no existe (intel)

El login de Sleeper va a `email.api.sleeper.app/graphql` con mutation
`login($email_or_phone_or_username, $password, $captcha)`: hay un
challenge anti-bot (HUMAN/PerimeterX) que emite el `$captcha`. Probado
A/B en la misma máquina, misma IP, mismo humano escribiendo: el navegador
propio pasa, cualquier ventana lanzada por automatización (Playwright,
aunque el humano escriba todo) falla incluso con el captcha resuelto.
El veredicto es contra la *sesión lanzada por automatización*, no contra
la entrada: ningún flag lo arregla. Por eso `core/capture.py` y los
endpoints `/auth/capture*` se eliminaron (2026-09-25) y el bookmarklet
`javascript:` se retiró (React/Chromium lo bloquean): la extensión con
cero automatización es la única ruta asistida.

## Retirado (historia, no usar)

- **Captura automática** (`/api/auth/capture`, `/cancel`, `/browser-install`,
  `core/capture.py`, Playwright): eliminada tras veredicto anti-bot
  estable contra sesiones automatizadas.
- **Bookmarklet** (enlace `javascript:` arrastrable): retirado; los
  navegadores modernos y React bloquean los `javascript:` URLs y el
  arrastre falla en la práctica.
