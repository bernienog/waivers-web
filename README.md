# Waivers (web)

Gestor de waivers de Sleeper, **local y privado**: ves tus ligas, armas
claims, los mandas a Sleeper y reconcilias el resultado. Todo corre en tu
máquina; no hay servidores intermedios.

Esta es la parte **web + backend** del proyecto. La app de escritorio
(instalador `.exe`) es solo para Windows y no está aquí: en Linux se usa
en el navegador, que es lo que hace [`./start.sh`](start.sh).

> **Beta privada.** Funciona, pero la interfaz sigue en ajuste. Úsalo con
> cuidado y no lo pongas en leagues donde un claim mal mandado te cueste.
>
> No afiliado con Sleeper ni con FantasyPros. Es un cliente de escritorio
> para una API pública.

**Versión actual: 0.1.37.** Es el mismo código que el instalador de
Windows; esta repo es la parte web + backend, sin el shell de escritorio.
El backend lo dice en `/api/health` (`version`).

## Qué hace

- **Hub**: digest de FantasyPros, watchlist, claims por liga en modo FAAB
  o por prioridad, y envío en un clic.
- **Mis Ligas**: roster, agentes libres, proyecciones de la semana, espacio
  de roster y fecha de limpieza de waivers.
- **Extensión de navegador**: lee tu sesión de Sleeper y se la pasa a la
  app en local. Es lo que evita que tengas que pegar tokens a mano.

## Requisitos

- **Python 3.12+**
- **Node LTS** (solo para compilar la interfaz; hace falta una vez)
- **Chromium** (Chrome o Brave). Ver la nota de la extensión más abajo.

No hay base de datos que instalar ni Docker. Todo queda en tu disco.

## Arrancar

```bash
git clone https://github.com/bernienog/waivers-web.git
cd waivers-web
./start.sh
```

Eso crea un `.venv`, instala lo de Python, compila la interfaz si hace
falta, levanta backend + UI en `http://localhost:3001` y abre el
navegador. Para recargar la UI al tocar código: `./start.sh --dev`.

## Conectar tu sesión (una vez)

Sin esto la app abre y no ve ninguna liga: no hay forma de hablar con
Sleeper en tu nombre.

1. Con la app corriendo, en la terminal: `cp .env.example .env`
2. Abre `http://localhost:3001`, ve al tutorial y pulsa **preparar la
   carpeta**. Copia la extensión a `Documentos/Waivers Connect` (o
   `~/.local/share/waivers/extension` en Linux) y te abre esa carpeta.
3. En Chrome: `chrome://extensions` → activa **Modo de desarrollador** →
   **Cargar descomprimida** → elige la carpeta que te acaba de abrir.
   Ojo: elige la **carpeta**, no un archivo de adentro.
4. Entra a sleeper.com, clic al icono de la extensión y
   **Enviar sesión a la app**.

El JWT se guarda en tu `.env` local y nunca sale de tu máquina.

### La extensión es solo Chromium

`Cargar descomprimida` no existe en Firefox. El flujo de la extensión, y por
lo tanto el de las escrituras, es Chromium-only. En Firefox se puede leer
todo lo que no escriba, pero no conectar la sesión.

## Dónde quedan tus datos

| Qué | Dónde |
|---|---|
| Sesión (JWT) | `.env`, en la raíz del repo. **Nunca lo commitees** |
| Base local | `data/waiver.db` (claims, wire, watchlist) |
| Cache de jugadores | `players_nfl.json`, se regenera solo |

Todo eso está en `.gitignore`. Para borrar todo: borra `.env` y `data/`.

## Desarrollo

```bash
./start.sh --dev        # vite en :5173 con HMR, backend en :3001
```

- `web/` — React + TypeScript + Vite
- `api/main.py` — rutas FastAPI
- `core/` — cliente de Sleeper (público y privado) y la base local
- `ext/` — la extensión, tal cual se carga en el navegador

Lee [`LINUX.md`](LINUX.md) para por qué esto corre en navegador y no como app
de escritorio, y [`docs/MANUAL.md`](docs/MANUAL.md) para el detalle de cada
pantalla.

## Privacidad

- El backend escucha solo en `127.0.0.1` y rechaza peticiones que no venga
  de la UI local o de la extensión.
- El JWT nunca se loguea y nunca sale de tu máquina.
- Las llamadas a Sleeper salen a un ritmo deliberadamente lento (un lock
  global con 150 ms entre requests). No subas ese número.

## Problemas

- **No aparecen ligas**: la sesión no está conectada. Arriba a la derecha
  está el botón **estado**, que dice qué falta.
- **"backend viejo"**: cerrá la app del todo. Si queda un proceso zombie
  ocupando el puerto 3001, `pkill -f uvicorn` y volvé a correr.
- **La extensión dice "app cerrada"**: abrí Waivers primero.
- **Cargar descomprimida se queja del manifiesto**: estás eligiendo un
  archivo en vez de la carpeta.
