import { useEffect, useState } from 'react';
import { api } from '../api';
import { Avatar, PosChip } from './PlayerBits';

interface P {
  player_id: string;
  name: string;
  pos?: string;
  team?: string;
}

export default function RosterPanel({ leagueId, tick, active }: { leagueId: string; tick: number; active: boolean }) {
  const [players, setPlayers] = useState<P[]>([]);

  // Gateado: el dashboard vive montado pero oculto; sin esto cada tick
  // global refetcheaba el roster aunque nadie lo mirara. Al volver se
  // carga (tick cambió mientras tanto).
  useEffect(() => {
    if (!active) return;
    api.roster(leagueId)
      .then((r: { roster_id: number; players: P[] }) => {
        setPlayers(r.players);
      })
      .catch(() => { setPlayers([]); });
  }, [leagueId, tick, active]);

  return (
    <div className="panel">
      <h3>Mi roster</h3>
      {players.map((p) => (
        <div key={p.player_id} className="row">
          <PosChip pos={p.pos} />
          <Avatar pid={p.player_id} name={p.name} />
          <span className="grow">{p.name}</span>
          <small className="muted">{p.pos} {p.team} · {p.player_id}</small>
        </div>
      ))}
      {players.length === 0 && <small className="muted">Sin datos.</small>}
    </div>
  );
}
