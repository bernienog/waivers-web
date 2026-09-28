import { useEffect, useState } from 'react';
import { api, esTauri, openExtensions, openFolder, type SetupState } from '../api';

/** Tutorial de conexión, a pantalla completa.
 *
 *  Es el estado "virgen": sin sesión de Sleeper no hay nada que ver (el hub
 *  saldría vacío y sin sentido), así que en vez de esconderlo detrás de un
 *  modal mostramos esto y ya. App.tsx no monta el hub ni pide ligas hasta
 *  que esto se cierra por conexión.
 *
 *  El mismo componente es el "ver los pasos" de quien YA está conectado y
 *  quiere cargar la extensión en otro navegador: por eso `onDone` es
 *  opcional (en modo referencia se cierra sin cambiar nada).
 *
 *  Todos los requisitos se leen de /api/setup (única fuente): motor,
 *  interfaz, datos, extensión disponible/preparada y sesión. No hay
 *  requisitos checados en otro lado (WebView2 lo revisa el splash de Rust). */

const EXT_URL = 'chrome://extensions';

function Shot({ src, alt, caption }: { src: string; alt: string; caption: string }) {
  // Si el drawing no está (o falla), el bloque desaparece en vez de dejar
  // una imagen rota: el texto debe bastar siempre.
  const [bad, setBad] = useState(false);
  if (bad) return null;
  return (
    <figure className="ob-shot" onErrorCapture={() => setBad(true)}>
      <img src={src} alt={alt} onError={() => setBad(true)} loading="lazy" />
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

export default function Onboarding({ onDone, onManual }: {
  onDone?: () => void;
  onManual: () => void;
}) {
  const [st, setSt] = useState<SetupState | null>(null);
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);

  const load = () => {
    void api.setup().then(setSt).catch((e: unknown) => setMsg(String(e)));
  };
  useEffect(load, []);

  /** La extensión POSTea a /api/auth/import: cuando eso pasa, el JWT ya
   *  está guardado. No esperamos a que el usuario avise, lo detectamos.
   *  Son llamadas locales (nada de Sleeper). */
  useEffect(() => {
    if (!waiting) return;
    let alive = true;
    const tick = () => {
      void api.setup()
        .then((s) => {
          if (!alive) return;
          setSt(s);
          const ok = s.jwt.status === 'valid' || s.jwt.status === 'expiring';
          if (ok) {
            setWaiting(false);
            setMsg('¡Conectado! Cargando tus ligas…');
            onDone?.();
          }
        })
        .catch(() => { /* el siguiente tick reintenta */ });
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(id); };
  }, [waiting, onDone]);

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      setMsg(`${what} copiada. Ya puedes pegarla.`);
    } catch {
      setMsg('No pude copiar. Cópialo a mano: ' + text);
    }
  }

  async function openExt() {
    setMsg('');
    try {
      const which = await openExtensions();
      setMsg(`${which} está abierto. Pega ${EXT_URL} en la barra de arriba.`);
      setWaiting(true);
    } catch (e: unknown) {
      setMsg(String(e));
    }
  }

  async function stage() {
    setBusy(true);
    setMsg('');
    try {
      const r = await api.stageExtension();
      const s = await api.setup().catch(() => null);
      if (s) setSt(s);
      setMsg('Carpeta lista. Te abrimos donde está para que elijas "' +
        (s?.stage_name ?? 'Waivers Connect') + '".');
      // El Explorador es del escritorio; en navegador solo se copia.
      if (esTauri()) await abrirParaElegir(r.path);
    } catch (e: unknown) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  /** Abre el PADRE de la carpeta de la extensión, no la carpeta.
   *
   * "Cargar descomprimida" necesita la CARPETA. Si abrimos la carpeta de
   * la extensión, el Explorador muestra los archivos sueltos
   * (manifest.json, popup.js...) y no hay nada que selecionar: el usuario
   * hace clic en un archivo y Chrome lo rechaza. Abriendo el padre, la
   * carpeta aparece como un elemento único y es obvio qué elegir.
   *
   * Con `void` en el onClick un `Err` del comando se perdia: rejection
   * sin capturar y cero feedback, o sea un boton que "no hace nada". Por
   * eso esto avisa como cualquier otro paso. */
  async function abrirParaElegir(stagePath: string) {
    try {
      await openFolder(padreDe(stagePath));
    } catch (e: unknown) {
      setMsg('No pude abrir el Explorador: ' + String(e) +
        ' Abre tu carpeta de Documentos a mano y elige "' +
        (st?.stage_name ?? 'Waivers Connect') + '".');
    }
  }

  function padreDe(p: string): string {
    const i = Math.max(p.lastIndexOf('\\'), p.lastIndexOf('/'));
    return i > 0 ? p.slice(0, i) : p;
  }

  const staged = !!st?.staged;
  const extMissing = !!st && !st.ext_ready;

  return (
    <div className="ob">
      <header className="ob-head">
        <div className="ob-brand">
          <span className="ob-logo">W</span>
          <div>
            <strong>waivers</strong>
            <small>gestor de waivers de Sleeper · local y privado</small>
          </div>
        </div>
        {st && <small className="muted">v{st.version} · motor en 127.0.0.1:{st.port}</small>}
      </header>

      <div className="ob-body">
        <h1>Vamos a conectarte</h1>
        <p className="ob-lede">
          Waivers necesita tu sesión de Sleeper para leer tus ligas y enviar
          claims. Se tarda un minuto, se hace una sola vez, y todo queda en
          esta máquina. Son tres pasos.
        </p>

        {/* ---- 1. carpeta ---- */}
        <section className="ob-step">
          <div className="ob-num">1</div>
          <div className="ob-main">
            <h2>Prepara la carpeta de la extensión</h2>
            {extMissing ? (
              <p className="ob-warn">
                Esta instalación no trae la extensión. Probablemente el build se
                armó sin `ext/`; reinstalá Waivers.
              </p>
            ) : staged ? (
              <p>
                Ya está. En el paso 3 eliges la carpeta{' '}
                <strong>{st?.stage_name ?? 'Waivers Connect'}</strong> de
                tus documentos.
                {esTauri() ? (
                  <> Te abrimos tu carpeta de Documentos para que la veas.</>
                ) : null}
              </p>
            ) : (
              <p>
                Copiamos la extensión a una carpeta normal de tus documentos
                para que puedas cargarla en tu navegador.
              </p>
            )}
            <div className="ob-acts">
              {staged ? (
                <>
                  {/* Abrir una carpeta es cosa del escritorio: una página
                      web no puede. En navegador dejamos copiar y lo decimos.
                      Ojo: se abre el PADRE, no la carpeta de la extensión:
                      dentro solo hay archivos sueltos y "Cargar
                      descomprimida" necesita la carpeta (ver
                      abrirParaElegir). */}
                  {esTauri() && (
                    <button className="primary"
                      onClick={() => st && void abrirParaElegir(st.stage_path)}>
                      ver la carpeta
                    </button>
                  )}
                  <button className={esTauri() ? '' : 'primary'}
                    onClick={() => st && void copy(st.stage_path, 'Ruta')}>
                    copiar ruta
                  </button>
                  {!esTauri() && (
                    <span className="ob-hint">desde el navegador no se puede abrir
                      una carpeta: cópiala y pégala en el selector.</span>
                  )}
                </>
              ) : (
                <button className="primary" onClick={() => void stage()}
                  disabled={busy || extMissing}>
                  {busy ? 'preparando…' : 'preparar la carpeta'}
                </button>
              )}
            </div>
          </div>
        </section>

        {/* ---- 2. extensión ---- */}
        <section className="ob-step">
          <div className="ob-num">2</div>
          <div className="ob-main">
            <h2>Cárgala en tu navegador</h2>
            <p>
              Es una extensión de Chrome/Edge que lee tu sesión de Sleeper y
              se la pasa a esta app. No hay tienda ni revisión de nadie: la
              cargas tú desde una carpeta, y por eso Chrome avisa
              &quot;extensión sin verificar&quot;. Es normal.
            </p>
            {/* La acción va PRIMERO, arriba del paso. Abajo, después de cuatro
                figuras, es tarde: el usuario ya la pasó por alto cuando por
                fin la ve. */}
            <div className="ob-acts">
              {/* En escritorio el botón lanza el navegador de verdad. En
                  navegador plano no se puede lanzar otro navegador, así que
                  el enlace normal a sleeper.com es lo que sí funciona. */}
              {esTauri() ? (
                <button className="primary" onClick={() => void openExt()}>
                  abrir Chrome
                </button>
              ) : (
                <a className="btnlink" href="https://sleeper.com" target="_blank"
                  rel="noreferrer">abrir sleeper.com</a>
              )}
              <div className="st-paste">
                <span className="url">{EXT_URL}</span>
                <button onClick={() => void copy(EXT_URL, 'URL')}>copiar</button>
              </div>
            </div>
            <p className="ob-hint">
              El navegador no deja que una app lo mande directo a esa página,
              así que hay que pegarla a mano: cópiala con el botón y pégala en
              la barra de direcciones.
            </p>
            <Shot src="/setup/01-extensions.svg" alt="La página de extensiones con el interruptor de Modo de desarrollador arriba a la derecha"
              caption="Abre la página de extensiones de tu navegador." />
            <Shot src="/setup/02-devmode.svg" alt="El interruptor de Modo de desarrollador encendido"
              caption="Enciende Modo de desarrollador (abajo a la derecha)." />
            <Shot src="/setup/03-loadunpacked.svg" alt="El botón Cargar descomprimida arriba a la izquierda"
              caption="Ahora aparece Cargar descomprimida, arriba a la izquierda." />
            <Shot src="/setup/04-picker.svg" alt="La ventana para elegir la carpeta"
              caption={'Elige la carpeta "' + (st?.stage_name ?? 'Waivers Connect') +
                '" que preparamos en el paso 1. La carpeta, no un archivo de adentro.'} />
          </div>
        </section>

        {/* ---- 3. sesión ---- */}
        <section className="ob-step">
          <div className="ob-num">3</div>
          <div className="ob-main">
            <h2>Conecta tu sesión</h2>
            <ol className="ob-list">
              <li>Entra a <b>sleeper.com</b> como siempre.</li>
              <li>Clic al icono <b>Waivers Connect</b> (fíjalo con el pin para tenerlo a la vista).</li>
              <li>Clic en <b>Enviar sesión a la app</b>.</li>
            </ol>
            <p>
              la app se conecta sola. Si dice <i>app cerrada</i>, abre Waivers
              y repítelo. Si dice <i>sin JWT</i>, esa pestaña no tenía sesión:
              inicia sesión en una pestaña normal, no en incógnito.
            </p>
            {waiting ? (
              <p className="ob-wait">
                <span className="ob-spin" /> esperando tu sesión… (esto se
                actualiza solo)
              </p>
            ) : null}
            <div className="ob-acts">
              <button className="primary" onClick={() => setWaiting(true)}>
                ya lo hice, revisa
              </button>
              <button onClick={onManual}>
                no puedo cargar la extensión: pegar mi sesión
              </button>
            </div>
          </div>
        </section>

        {msg && <p className="ob-msg">{msg}</p>}

        {/* Requisitos, aquí mismo: en estado virgen este tutorial ES la
            pantalla de estado, no hay que abrir otro modal. */}
        {st && (
          <section className="ob-reqs">
            <h3>Requisitos</h3>
            <ul>
              <li><b className="st-ok">✓</b> Motor local (v{st.version})</li>
              <li><b className="st-ok">✓</b> Interfaz cargada</li>
              <li><b className={st.db_exists ? 'st-ok' : 'st-bad'}>
                {st.db_exists ? '✓' : '·'}</b> Tus datos
                <small className="muted"> {st.data_dir}</small>
              </li>
              <li><b className={staged ? 'st-ok' : 'st-bad'}>
                {staged ? '✓' : '·'}</b> Extensión preparada</li>
              <li><b className="st-bad">·</b> Sesión de Sleeper
                <small className="muted"> falta</small></li>
            </ul>
            <small className="muted">
              Windows 10/11 con WebView2 (viene con el sistema). Todo local: tu
              sesión y tus ligas no salen de tu máquina.
            </small>
          </section>
        )}

        {onDone && (
          <div className="ob-foot">
            <button onClick={onDone}>cerrar estos pasos</button>
          </div>
        )}
      </div>
    </div>
  );
}
