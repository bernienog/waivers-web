import { useEffect, useState } from 'react';
import { api, esTauri, openFolder, type SetupState } from '../api';
import { useEsc } from '../useEsc';

/** Panel "estado". DOS estados, sin mezclar:
 *
 *  - Conectado  → solo el parte: qué corre y dónde está cada cosa.
 *                 Cero botones que "no hacen nada".
 *  - Sin conectar → el resumen, con "ver los pasos" (el tutorial completo
 *                   vive en Onboarding; aquí no se duplica).
 *
 * El tutorial completo (carpeta + navegador + login) se abre desde acá con
 * "ver los pasos", que además sirve para quien ya está conectado pero
 * necesita cargar la extensión en otro navegador. */

function Line({ ok, label, value, onClick }: {
  ok: boolean; label: string; value: string; onClick?: () => void;
}) {
  return (
    <div className="st-line">
      <b className={ok ? 'st-ok' : 'st-bad'}>{ok ? '✓' : '✗'}</b>
      <div className="grow">
        <div>{label}</div>
        <small className="muted">{value}</small>
      </div>
      {/* Abrir una carpeta solo existe en la app de escritorio: en el
          navegador el valor se queda en pantalla para copiarlo. */}
      {onClick && esTauri() && <button className="btnlight" onClick={onClick}>abrir</button>}
    </div>
  );
}

export default function SystemCheck({ onClose, onShowSetup }: {
  onClose: () => void;
  onShowSetup: () => void;
}) {
  const [st, setSt] = useState<SetupState | null>(null);
  const [msg, setMsg] = useState('');

  const load = () => {
    void api.setup().then(setSt).catch((e: unknown) => setMsg(String(e)));
  };
  useEffect(load, []);
  useEsc(onClose);

  async function openPath(p: string) {
    setMsg('');
    try {
      await openFolder(p);
    } catch (e: unknown) {
      setMsg(`${String(e)}`);
    }
  }

  const j = st?.jwt;
  const jwtOk = j?.status === 'valid' || j?.status === 'expiring';
  const jwtLine = !j || j.status === 'missing'
    ? 'sin conectar'
    : j.status === 'expired' ? 'caducada — vuelve a conectarla'
    : j.status === 'invalid' ? 'no válida — vuelve a conectarla'
    : j.status === 'expiring' ? `conectada · caduca en ${j.hours_left}h`
    : `conectada · ${j.hours_left}h de vida`;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal st-modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} title="cerrar (esc)">×</button>
        <h3>estado</h3>

        {!st && <div><small className="muted">leyendo…</small></div>}

        {st && (
          <div className="st-lines">
            <Line ok label="Motor local" value={`v${st.version} · 127.0.0.1:${st.port}`} />
            <Line ok label="Interfaz" value="pintada" />
            <Line ok={true} label="Cómo abriste la app"
              value={esTauri() ? 'escritorio (app instalada)'
                : 'navegador en 127.0.0.1:3001'} />
            <Line ok={st.db_exists} label="Tus datos"
              value={st.db_exists ? st.data_dir : `se creará en ${st.data_dir}`}
              onClick={() => void openPath(st.data_dir)} />
            <Line ok={jwtOk} label="Sesión de Sleeper" value={jwtLine} />
            <Line ok={st.staged} label="Extensión del navegador"
              value={st.staged ? st.stage_path
                : st.ext_ready ? 'falta prepararla' : 'no viene en esta versión'}
              onClick={st.staged ? () => void openPath(st.stage_path) : undefined} />
          </div>
        )}

        {/* ---- Sin sesión CONECTADA ALGUNA VEZ (caducada / no válida): lo
               que falta es re-conectar, no instalar la extensión. El guion
               de instalación vive en Onboarding ("ver los pasos"). ---- */}
        {st && !jwtOk && (
          <>
            <hr />
            <div className="st-foot"><small className="muted">
              Tu sesión de Sleeper caduca cada cierto tiempo. Vuelve a
              conectarla con la extensión: entra a sleeper.com, clic al icono
              Waivers Connect, <b>Enviar sesión a la app</b>. Con la app
              abierta se conecta sola.
            </small></div>
            <div className="ob-acts">
              <button className="primary" onClick={onShowSetup}>ver los pasos</button>
              <button className="btnlight" onClick={load}>revisar</button>
            </div>
          </>
        )}

        {/* ---- Conectado: solo el parte, cero botones inertes. ---- */}
        {st && jwtOk && (
          <div className="st-foot">
            <small className="muted">
              Todo listo. ¿Necesitas cargarla en otro navegador, o volver a
              instalarla?{' '}
              <button className="watchlink" onClick={onShowSetup}>ver los pasos</button>
            </small>
          </div>
        )}

        {msg && <div className="st-msg"><small>{msg}</small></div>}

        <div className="st-foot">
          <small className="muted">
            Windows 10/11 (WebView2 ya viene en el sistema). Todo local: tu
            sesión y tus ligas no salen de tu máquina.
          </small>
        </div>
        <div className="btnrow">
          <button onClick={onClose}>cerrar</button>
        </div>
      </div>
    </div>
  );
}
