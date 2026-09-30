# Waivers en Linux (y macOS)

La app de **escritorio solo existe para Windows** (un `.exe`). En Linux la
misma aplicación corre en **modo navegador**: el motor sirve la interfaz en
`127.0.0.1:3001` y tú la abres en el navegador. No es una versión recortada:
es el mismo código, la misma interfaz, los mismos datos.

## Qué necesitas

| | |
|---|---|
| Python | 3.12 o superior, con `venv` |
| Node | LTS — **solo para compilar la interfaz la primera vez** (ver abajo) |
| Navegador | Chrome, Chromium, Brave o Edge (ver la nota de la extensión) |

No necesitas Rust, ni WebView2, ni Docker, ni cuenta de nada.

## Arrancar

```bash
git clone <repo>
cd waivers-project
./start.sh
```

`start.sh` se encarga de todo: crea un `.venv`, instala las dependencias,
compila la interfaz si falta, levanta el servidor y abre el navegador. Es el
gemelo de `start.bat` de Windows.

Opciones:

```bash
./start.sh --dev         # interfaz en :5173 con recarga en caliente
./start.sh --no-browser  # no abrir el navegador al final
```

### Por qué pide Node

`web/dist` (la interfaz compilada) está en `.gitignore`, así que en un clon
nuevo hay que compilarla una vez: de ahí el `npm install` + `npm run build`
que hace `start.sh`. **Después de esa primera vez, `start.sh` ya no vuelve a
usar Node.** En Windows esto no aplica porque el `.exe` ya trae todo.

## Conectar tu sesión (una vez)

Waivers necesita tu sesión de Sleeper para leer tus ligas. No hay login en la
app: eso lo hace una extensión del navegador, en 4 pasos.

1. La app abre su tutorial. Pulsa **preparar la carpeta**. En modo navegador
   no hay botón de "abrir carpeta" (una página web no puede abrir carpetas
   del disco), así que pulsa **copiar ruta**.
2. Abre `chrome://extensions` en el navegador (en Firefox no funciona, ver
   más abajo).
3. Activa **Modo de desarrollador** (arriba a la derecha).
4. **Cargar descomprimida** → pega la ruta copiada → elige la carpeta.
5. Entra a `sleeper.com`, clic al icono **Waivers Connect** →
   **Enviar sesión a la app**.

La app se conecta sola y sale del tutorial.

## La extensión: solo navegadores Chromium

Waivers Connect es una extensión **Manifest V3** que se carga desde una
carpeta. Chrome, Chromium, Brave y Edge lo soportan en Linux.

**Firefox no.** Firefox no permite cargar extensiones MV3 sin empaquetar de
otra forma, así que con Firefox esta app no se puede conectar todavía. No es
un detalle de configuración: es un límite del navegador.

## Ajustes de `.env` (opcionales)

Además de las variables de la sesión, hay tres que afinan la ventana de
waivers de cada liga. **No hace falta tocar ninguna** en el uso normal.

| variable | para qué |
|---|---|
| `WAIVERS_NEVER_FREE` | ids de ligas, separados por coma, que **nunca** abren free agency. Para las que el UI de Sleeper y el API se contradicen. |
| `WAIVER_GRACE_MIN` | margen tras el proceso de waivers (default 30). Sleeper resuelve por lotes y en un día pesado se pasa. |
| `WAIVER_BORDER_H` | horas antes del corte en las que se bloquea (default 6). Ante duda se bloquea: es el error barato. |

La hora de proceso **no se configura**: sale de `settings.daily_waivers_hour`
del API, que viene en hora Pacífica, y se convierte con el offset real de
`America/Los_Angeles` (que tiene DST, a diferencia de México). El detalle
de por qué está en [`plans/WAIVERS-WINDOW.md`](plans/WAIVERS-WINDOW.md).

## Dónde quedan tus datos

En modo navegador todo vive **dentro del clon**, no en una carpeta de
Windows:

- Sesión (JWT): `.env` en la raíz del repo.
- Base de datos: `data/waiver.db`.
- Extensión copiada: `~/.local/share/waivers/extension`.

Para empezar de cero: borra `.env` y `data/`, y quita la extensión de
`chrome://extensions`.

## Lo que NO tiene versión nativa

El `.exe` de Windows empaqueta un motor y una ventana. En Linux el motor es
el mismo pero la ventana es tu navegador, así que un par de cosas no
existen y la interfaz las esconde en vez de mostrar botones rotos:

- **abrir carpeta** → reemplazado por *copiar ruta*.
- **abrir Chrome** → reemplazado por un enlace normal a `sleeper.com`.

Todo lo demás (hub, watchlist, claims, multibid, agentes libres,
proyecciones, verificar) funciona igual.

## Si algo falla

- `El backend no respondio en :3001` → mira la salida de uvicorn en la
  misma terminal.
- Puerto ocupado → `fuser -k 3001/tcp` y corre `./start.sh` otra vez.
- La interfaz sale en blanco → `./start.sh --dev` y abre
  `http://localhost:5173` para ver el error en consola.
- Cualquier otra cosa: el botón **estado** de la app dice qué falta, y esa
  pantalla está hecha para pegarla tal cual.
