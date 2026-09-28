import { useRef, useState } from 'react';
import { api, codigoDe, textoDeError } from '../api';
import { useEsc } from '../useEsc';

/** Abre en el navegador del sistema. En la app desktop window.open está
 * bloqueado (WebView): se usa el plugin opener. Si TODO falla, se muestra
 * la URL como texto copiable: jamás silencio. */
async function openSleeper(setFail: (v: boolean) => void) {
  setFail(false);
  try {
    const { openUrl } = await import('@tauri-apps/plugin-opener');
    await openUrl('https://sleeper.com');
    return;
  } catch {
    /* cae al fallback navegador */
  }
  const w = window.open('https://sleeper.com', '_blank', 'noopener');
  if (!w) setFail(true);
}

const JWT_RE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export default function AuthModal({ onClose, onSaved }: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [jwt, setJwt] = useState('');
  const [session, setSession] = useState('');
  const [userId, setUserId] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [openFail, setOpenFail] = useState(false);
  const [done, setDone] = useState(false);
  useEsc(onClose);
  const autoFor = useRef('');

  async function save(value: string) {
    const v = (value || '').trim().replace(/^"|"$/g, '');
    if (!v || busy) return;
    setBusy(true);
    setMsg('conectando…');
    setDone(false);
    try {
      const r = await api.authSave({ jwt: v, session: session.trim(), user_id: userId.trim() });
      const st = r.jwt.status;
      if (st === 'valid' || st === 'expiring') {
        setMsg('conectado ✓ vuelve y pulsa ↻');
        setDone(true);
        onSaved();
      } else {
        setMsg(st === 'expired'
          ? 'ese token ya caducó. Vuelve a iniciar sesión en sleeper.com y pega el nuevo.'
          : 'ese token no sirve. Revisa que copiaste todo, sin comillas.');
      }
    } catch (e: unknown) {
      setMsg(textoDeError(e, codigoDe(e)));
    } finally {
      setBusy(false);
    }
  }

  /** Pegar = conectar: en cuanto el texto tiene forma de JWT, se envía
   * solo. Cero botones para el caso normal. */
  function onPasteText(v: string) {
    setJwt(v);
    setDone(false);
    const t = v.trim().replace(/^"|"$/g, '');
    if (JWT_RE.test(t) && autoFor.current !== t) {
      autoFor.current = t;
      void save(t);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} title="cerrar (esc)">×</button>
        <h3>conectar Sleeper</h3>
        <div><small className="muted">
          ¿Waivers Connect? Un clic en <b>Enviar sesión</b> y listo, sin pegar nada.
        </small></div>
        <label>
          <small className="muted">o pega tu JWT aquí (se conecta solo)</small>
          <textarea rows={5} style={{ width: '100%', fontSize: '15px' }} value={jwt}
            onChange={(e) => onPasteText(e.target.value)} placeholder="eyJ…" />
        </label>
        {msg && <div><small className="muted">{msg}</small></div>}
        {done ? (
          <div className="btnrow">
            <button className="primary" onClick={onClose}>listo: cerrar</button>
          </div>
        ) : (
          <div className="btnrow">
            <button className="primary" onClick={() => void save(jwt)}
              disabled={busy || !jwt.trim()}>
              {busy ? '…' : 'conectar'}
            </button>
            <button onClick={onClose}>cerrar</button>
          </div>
        )}
        <details>
          <summary><small className="muted">avanzado (casi nunca hace falta)</small></summary>
          <div>
            <button onClick={() => void openSleeper(setOpenFail)}>
              abrir sleeper.com
            </button>
            {openFail && (
              <div><small className="muted">
                no pude abrirlo: copia https://sleeper.com a tu navegador
              </small></div>
            )}
          </div>
          <label>
            <small className="muted">sesión (cookie sleeper-web-session, opcional)</small>
            <input style={{ width: '100%' }} value={session}
              onChange={(e) => setSession(e.target.value)} placeholder="…" />
          </label>
          <label>
            <small className="muted">user id (dígitos de tu perfil, si la app lo pide)</small>
            <input style={{ width: '100%' }} value={userId}
              onChange={(e) => setUserId(e.target.value)} placeholder="…" />
          </label>
        </details>
      </div>
    </div>
  );
}
