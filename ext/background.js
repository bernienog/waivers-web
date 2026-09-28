/* Waivers Connect: relay en el service worker (MV3).
 *
 * El popup NO habla red directa: pide aquí y el worker POSTea a la URL
 * FIJA de abajo (allowlist). Sin esto, la CSP de la página y el ciclo de
 * vida del popup pueden matar el fetch a mitad de clic. La URL jamás viene
 * del contenido: solo jwt/sesión viajan en el mensaje.
 */

const ALLOW = 'http://127.0.0.1:3001/api/auth/import';
const JWT_RE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== 'wvr-import') return false;
  // Solo acepta de nuestras propias páginas (popup). Nada de la web.
  const from = String((sender && sender.url) || '');
  if (!from.startsWith('chrome-extension://')) {
    sendResponse({ ok: false, error: 'origen no permitido' });
    return false;
  }
  const jwt = String(msg.jwt || '').trim();
  const session = String(msg.session || '').trim();
  if (!JWT_RE.test(jwt)) {
    sendResponse({ ok: false, error: 'sin JWT válido: ¿sesión iniciada?' });
    return false;
  }
  fetch(ALLOW, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jwt, session }),
  })
    .then(async (r) => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        sendResponse({
          ok: false,
          error: 'rechazado: ' + ((data && data.detail && data.detail.error) || r.status),
        });
        return;
      }
      sendResponse({ ok: true, leagues: data.leagues });
    })
    .catch(() => {
      sendResponse({ ok: false, error: 'app cerrada: abre Waivers e inténtalo de nuevo' });
    });
  return true; // respuesta asíncrona
});
