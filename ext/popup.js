/* Waivers Connect (MV3, sin store, sideload).
 * Lee localStorage.token EXACTO de sleeper.com (+ cookie, incl. HttpOnly
 * vía chrome.cookies), quita comillas JSON y lo pasa al service worker,
 * que lo POSTea a la app local (URL fija, allowlist). La app lo prueba
 * contra Sleeper ANTES de guardar. Cero estado propio: todo vive en el
 * .env de la app. Sin red salvo sleeper.com y localhost.
 */

const $ = (id) => document.getElementById(id);

function setStatus(msg, cls) {
  const el = $('status');
  el.textContent = msg;
  el.className = cls || '';
}

async function activeTab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

/* Corre en contexto de la página (ve su localStorage). */
function readPage() {
  let jwt = '';
  try {
    jwt = window.localStorage.getItem('token') || '';
  } catch (e) {
    return { jwt: '', session: '', lsError: true };
  }
  // Sleeper lo guarda JSON-quoteado ("eyJ…"): sin comillas no lo acepta.
  if (jwt.length >= 2 && jwt.startsWith('"') && jwt.endsWith('"')) {
    jwt = jwt.slice(1, -1);
  }
  let session = '';
  try {
    for (const part of String(document.cookie || '').split(';')) {
      const i = part.indexOf('=');
      if (i > 0 && part.slice(0, i).trim() === 'sleeper-web-session') {
        session = decodeURIComponent(part.slice(i + 1).trim());
        break;
      }
    }
  } catch (e) {
    /* cookie inaccesible: se intenta vía chrome.cookies abajo */
  }
  return { jwt, session, lsError: false };
}

async function harvest() {
  const tab = await activeTab();
  if (!tab || !/sleeper\.com/.test(tab.url || '')) {
    throw new Error('abre sleeper.com e inicia sesión primero');
  }
  const inj = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: readPage,
  });
  const got = (inj && inj[0] && inj[0].result) || {};
  if (got.lsError) {
    throw new Error('página bloquea lectura (recarga sleeper.com)');
  }
  let jwt = got.jwt || '';
  let session = got.session || '';
  // HttpOnly (document.cookie no la ve): vía API de cookies.
  try {
    const c = await chrome.cookies.get({
      url: 'https://sleeper.com',
      name: 'sleeper-web-session',
    });
    if (c && c.value) session = c.value;
  } catch (e) {
    /* opcional: se prueba solo con JWT */
  }
  if (!jwt) {
    throw new Error('sin JWT en esta pestaña: ¿sesión iniciada?');
  }
  if (!/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(jwt)) {
    throw new Error('el valor no tiene forma de JWT (¿cambió Sleeper?)');
  }
  return { jwt, session };
}

  async function onSend() {
  const btn = $('send');
  btn.disabled = true;
  try {
    setStatus('leyendo sleeper…');
    const body = await harvest();
    setStatus('enviando a la app…');
    let r;
    try {
      r = await chrome.runtime.sendMessage({ type: 'wvr-import', ...body });
    } catch (e) {
      setStatus('worker caído: recarga la extensión e inténtalo de nuevo', 'bad');
      return;
    }
    if (!r || !r.ok) {
      setStatus((r && r.error) || 'sin respuesta del worker', 'bad');
      return;
    }
    setStatus('conectado ✓ (' + (r.leagues ?? '?') + ' ligas)', 'ok');
  } catch (e) {
    setStatus(String((e && e.message) || e), 'bad');
  } finally {
    btn.disabled = false;
  }
}

async function onCopy() {
  try {
    setStatus('leyendo sleeper…');
    const { jwt } = await harvest();
    await navigator.clipboard.writeText(jwt);
    setStatus('JWT copiado: pégalo en la app (conectar)', 'ok');
  } catch (e) {
    setStatus(String((e && e.message) || e), 'bad');
  }
}

$('send').addEventListener('click', () => void onSend());
$('copy').addEventListener('click', () => void onCopy());
