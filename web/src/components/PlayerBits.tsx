import { useEffect, useState } from 'react';

const KNOWN_POS = ['QB', 'RB', 'WR', 'TE', 'K', 'DEF'];

export function PosChip({ pos }: { pos?: string | null }) {
  const p = (pos ?? '').toUpperCase();
  const cls = KNOWN_POS.includes(p) ? p : 'NA';
  return <span className={`pos pos-${cls}`}>{pos ?? '?'}</span>;
}

/** Pids que ya dieron 404 (DSTs/unmatched): no re-pedirlos en cada
 * montaje — el <img> fallido se reintentaba fila tras fila. */
const deadPids = new Set<string>();

export function Avatar({ pid, name }: { pid: string; name: string }) {
  const [ok, setOk] = useState(() => !deadPids.has(pid));
  useEffect(() => setOk(!deadPids.has(pid)), [pid]);
  if (!ok || !pid) return null;
  return (
    <img
      className="avatar" alt={name}
      src={`https://sleepercdn.com/content/nfl/players/${pid}.jpg`}
      onError={() => { deadPids.add(pid); setOk(false); }}
    />
  );
}

/** Tarjeta para los modales de confirmar: 2 filas — foto + nombre arriba,
 *  chip de posición (con su color) + equipo abajo. Para ver a quién estás
 *  metiendo sin leer la frase. */
export function PlayerCard({ pid, name, pos, team }: {
  pid?: string | null; name: string; pos?: string | null; team?: string | null;
}) {
  return (
    <div className="pcard">
      <div className="top">
        {pid ? <Avatar pid={pid} name={name} /> : null}
        <span className="nm">{name}</span>
      </div>
      <div className="bot">
        {pos ? <PosChip pos={pos} /> : null}
        {team ? <small className="muted">{team}</small> : null}
      </div>
    </div>
  );
}
