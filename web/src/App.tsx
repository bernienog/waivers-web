import { useEffect, useState } from 'react';
import { api, codigoDe, fmtTime, textoDeError, type Freshness } from './api';
import type { League } from './types';
import RosterPanel from './components/RosterPanel';
import FreeAgentsPanel, { type FA } from './components/FreeAgentsPanel';
import LeagueClaimModal from './components/LeagueClaimModal';
import AuthModal from './components/AuthModal';
import Onboarding from './components/Onboarding';
import SystemCheck from './components/SystemCheck';
import PickupModal from './components/PickupModal';
import Hub from './components/Hub';

type Feature = 'hub' | 'dashboard';

const TABS: { key: Feature; label: string }[] = [
  { key: 'hub', label: '⌂ inicio' },
  { key: 'dashboard', label: 'Mis Ligas' },
];

export default function App() {
  const [feature, setFeature] = useState<Feature>('hub');
  const [leagues, setLeagues] = useState<League[]>([]);
  const [lid, setLid] = useState('');
  const [err, setErr] = useState('');
  const [claimOpen, setClaimOpen] = useState(false);
  const [faAdd, setFaAdd] = useState<{ pid: string; name: string } | null>(null);
  const [faPickup, setFaPickup] = useState<FA | null>(null);
  const [flash, setFlash] = useState('');
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  /** Tick POR LIGA: se mueve solo para la liga refrescada. El global
   *  (`updatedAt`) queda para el ↻ de todas. */
  const [leagueTick, setLeagueTick] = useState<Record<string, number>>({});
  /** La lista de ligas vino del snapshot local (Sleeper no respondió). */
  const [leaguesStale, setLeaguesStale] = useState(false);
  const [fresh, setFresh] = useState<Freshness | null>(null);
  const [freshFailed, setFreshFailed] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  /** Estado virgen: sin JWT no hay hub que mostrar. */
  const [virgin, setVirgin] = useState<boolean | null>(null);
  const [checkOpen, setCheckOpen] = useState(false);
  const [beVer, setBeVer] = useState('');

  function reloadFresh() {
    void api.freshness().then((f) => { setFresh(f); setFreshFailed(false); })
      .catch(() => setFreshFailed(true));
  }

  async function loadLeagues() {
    setLoading(true);
    setErr('');
    try {
      const r = await api.leagues();
      setLeagues(r.leagues);
      setLid((cur) => (r.leagues.some((l) => l.league_id === cur)
        ? cur : (r.leagues[0]?.league_id ?? '')));
      setLeaguesStale(r.stale);
      setUpdatedAt(Date.now());
    } catch (e: unknown) {
      setErr(textoDeError(e, codigoDe(e)));
    } finally {
      setLoading(false);
    }
  }

  /** ↻ global: Tier-1 verify (solo-lectura remota) + ligas + freshness. */
  async function refreshAll() {
    setLoading(true);
    setErr('');
    try {
      try {
        setFlash('verificando claims…');
        const v = await api.verify();
        const parts = [`${v.checked} vigentes`];
        if (v.won || v.lost) parts.push(`${v.won}W/${v.lost}L`);
        if (v.vanished) parts.push(`${v.vanished} vanished`);
        if (v.skipped.length) parts.push(`${v.skipped.length} omitidas`);
        setFlash(`verify: ${parts.join(' · ')} — cargando ligas…`);
      } catch (e: unknown) {
        setFlash(`verify falló: ${textoDeError(e, codigoDe(e))} (sigo con ligas)`);
      }
      const r = await api.leagues();
      setLeagues(r.leagues);
      setLid((cur) => (r.leagues.some((l) => l.league_id === cur)
        ? cur : (r.leagues[0]?.league_id ?? '')));
      setLeaguesStale(r.stale);
      setUpdatedAt(Date.now());
      await api.freshness().then((f) => { setFresh(f); setFreshFailed(false); })
        .catch(() => setFreshFailed(true));
    } catch (e: unknown) {
      setErr(textoDeError(e, codigoDe(e)));
    } finally {
      setLoading(false);
    }
  }

  /** Refresh de UNA liga (filas Hub + panel dashboard). Verify + row en
   *  paralelo; verify se omite en light (tras crear: nada submitted).
   *
   *  OJO: esto NO sube el tick global. Antes sí, y como el Hub escucha
   *  `[leagues, tick]`, refrescar una liga re-tiraba pending+clears de
   *  TODAS (3 requests x N ligas por clic). Ahora la liga tocada se
   *  refresca sola (leagueTick) y la ola de todas queda para el ↻ global. */
  async function refreshLeague(id: string, opts?: { verify?: boolean }) {
    const doVerify = opts?.verify !== false;
    const verifyP = doVerify ? api.verify(id).catch(() => null) : Promise.resolve(null);
    const [row] = await Promise.all([api.league(id), verifyP]);
    setLeagues((ls) => ls.map((l) => (l.league_id === id ? row : l)));
    setLeagueTick((m) => ({ ...m, [id]: Date.now() }));
  }

  useEffect(() => {
    void api.freshness().then((f) => {
      setFresh(f); setFreshFailed(false);
      // Virgen = sin sesión. Primero se decide la puerta de entrada: pedir
      // ligas sin JWT solo produce un error detrás del tutorial.
      const isVirgin = f.jwt.status === 'missing';
      setVirgin(isVirgin);
      if (!isVirgin) void loadLeagues();
    }).catch(() => { setFreshFailed(true); setVirgin(false); });
    void api.health().then((h) => setBeVer(h.version ?? '')).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const league = leagues.find((l) => l.league_id === lid);

  /** Estado virgen: pantalla completa de conexión. No hay hub detrás. */
  if (virgin) {
    // Al terminar hay que reverificar TODO: `fresh` venía del arranque con
    // el JWT ausente, y sin refrescarlo el banner "JWT missing" se queda
    // pegado sobre la app ya conectada. Primero la sesión, y recién
    // después las ligas (que necesitan el JWT ya guardado).
    const finishOnboarding = () => {
      setVirgin(false);
      void reloadFresh();
      void loadLeagues();
    };
    return (
      <>
        <Onboarding onDone={finishOnboarding}
          onManual={() => { setVirgin(false); setAuthOpen(true); }} />
        {authOpen && (
          <AuthModal onClose={() => setAuthOpen(false)} onSaved={finishOnboarding} />
        )}
      </>
    );
  }


  return (
    <>
      <header className="topbar">
        <strong>waivers</strong>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.key} className={feature === t.key ? 'active' : ''} onClick={() => setFeature(t.key)}>
              {t.label}
            </button>
          ))}
        </nav>
        <button className="iconbtn" onClick={() => void refreshAll()} disabled={loading} title="Verificar claims + reverificar ligas (Sleeper)">
          {loading ? '…' : '↻'}
        </button>
        {updatedAt !== null && (
          <small className="muted">última actualización {new Date(updatedAt).toLocaleTimeString()}</small>
        )}
        <button className="iconbtn" onClick={() => setCheckOpen(true)}
          title="estado del sistema (qué falta, si algo)">
          estado
        </button>
        {loading && leagues.length === 0 ? (
          <div className="skel" style={{ width: 280, height: 34 }} />
        ) : (
          <select value={lid} onChange={(e) => setLid(e.target.value)}>
            {leagues.map((l) => (
              <option key={l.league_id} value={l.league_id}>
                {l.name} · {l.record} · {l.mode} · saldo {l.budget_left ?? 'n/a'}
              </option>
            ))}
          </select>
        )}
      </header>
      {loading && <div className="loadbar"><div /></div>}

      {err && <div className="panel"><span className="badge bad">{err}</span>
        <small className="muted"> — si no se arregla, cierra Waivers y vuelve a abrirlo.</small></div>}

      {/* La lista se está sirviendo del último snapshot: se dice, porque
          un record o un saldo viejo puede cambiar una decisión. */}
      {leaguesStale && (
        <div className="panel"><span className="badge warn">caché</span>{' '}
          <small className="muted">Sleeper no respondió: esto es lo último que se leyó.
            No confíes en saldos ni records hasta que vuelva.</small></div>
      )}


      {loading && leagues.length === 0 && (
        <div className="panel">
          {[0, 1, 2].map((i) => (
            <div key={i} className="row">
              <div className="skel circle" style={{ width: 30, height: 30 }} />
              <div className="skel" style={{ flex: 1, height: 16 }} />
              <div className="skel" style={{ width: 64, height: 20 }} />
              <div className="skel" style={{ width: 34, height: 34 }} />
              <div className="skel" style={{ width: 34, height: 34 }} />
            </div>
          ))}
        </div>
      )}

      {freshFailed && !fresh && leagues.length > 0 && (
        <div className="panel"><span className="badge claim">sin auditoría</span>{' '}
          <small className="muted">no pude verificar tus claims. Pulsa ↻ para reintentar.</small></div>
      )}
      {fresh && (fresh.jwt.status === 'expired' || fresh.jwt.status === 'missing' || fresh.jwt.status === 'invalid') && (
        <div className="panel"><span className="badge bad">
          {fresh.jwt.status === 'missing' ? 'sesión no conectada'
            : fresh.jwt.status === 'expired' ? 'sesión caducada'
            : 'sesión inválida'}
        </span>{' '}
          <button onClick={() => setAuthOpen(true)}>conectar</button>{' '}
          <small className="muted">sin esto no hay envíos ni pendings.</small></div>
      )}
      {fresh && fresh.jwt.status === 'expiring' && (
        <div className="panel"><span className="badge claim">JWT expira</span>{' '}
          <small className="muted">
            quedan {fresh.jwt.hours_left ?? '?'}h.
          </small>{' '}
          <button onClick={() => setAuthOpen(true)}>re-conectar</button>
        </div>
      )}
      {fresh && fresh.claims.stale && (
        <div className="panel">
          <span className="badge claim">{fresh.claims.submitted} enviados sin verificar</span>{' '}
          <button onClick={() => void refreshAll()}>verificar ahora</button>
        </div>
      )}

      {/* Tabs siempre montados: cambiar de tab no refetchea nada.
          El reload solo ocurre vía ↻ global / por liga (tick). */}
      <div style={{ display: feature === 'hub' ? undefined : 'none' }}>
        <Hub
          leagues={leagues}
          tick={updatedAt ?? 0}
          leagueTick={leagueTick}
          active={feature === 'hub'}
          onOpenLeague={(id) => { setLid(id); setFeature('dashboard'); }}
          onLeagueRefresh={(id, opts) => refreshLeague(id, opts)}
        />
      </div>
      <div style={{ display: feature === 'dashboard' ? undefined : 'none' }}>
      {lid && (
        <>
          <div className="panel">
            <span className="grow"><strong>{league?.name}</strong></span>{' '}
            <span className={`badge ${league?.mode}`}>{league?.mode}</span>{' '}
            {league?.mode === 'claim' && league?.waiver_priority != null && (
              <span className="badge claim">prio #{league.waiver_priority}</span>
            )}{' '}
            <small className="muted">
              record {league?.record} ·
              saldo {league?.budget_left ?? 'n/a'} · min {league?.bid_min}
              {league?.roster_space ? ` · slots libres ${league.roster_space.free}` : ''}
              {league?.roster_space && league.roster_space.reserve_capacity > 0
                ? ` · IR ${league.roster_space.reserve_free}` : ''}
              {league?.roster_space && league.roster_space.taxi_capacity > 0
                ? ` · taxi ${league.roster_space.taxi_free}` : ''}
              {league?.fetched_at ? ` · última actualización ${fmtTime(league.fetched_at)}` : ''}
            </small>{' '}
            <button className="iconbtn" onClick={() => void refreshLeague(lid)} disabled={loading} title="Refresh esta liga">
              ↻
            </button>
          </div>
          {flash && <div className="panel"><small className="muted">{flash}</small></div>}
          <div className="cols">
            <RosterPanel leagueId={lid} tick={Math.max(updatedAt ?? 0, leagueTick[lid] ?? 0)} active={feature === 'dashboard'} />
            <FreeAgentsPanel
              leagueId={lid}
              tick={Math.max(updatedAt ?? 0, leagueTick[lid] ?? 0)}
              active={feature === 'dashboard'}
              onAdd={(pid, name) => { setFaAdd({ pid, name }); setClaimOpen(true); }}
              onPickup={(p) => setFaPickup(p)}
              positions={league?.roster_positions}
            />
          </div>
        </>
      )}
      </div>
      {claimOpen && league && (
        <LeagueClaimModal
          league={league}
          leagues={leagues}
          preselectedAdd={faAdd?.pid}
          preselectedName={faAdd?.name ?? ''}
          onClose={() => { setClaimOpen(false); setFaAdd(null); }}
          onSent={() => { api.notifyClaimsChanged(league.league_id, faAdd?.pid); setClaimOpen(false); setFaAdd(null); setUpdatedAt(Date.now()); void refreshLeague(league.league_id, { verify: false }); }}
          onMultiSent={() => setUpdatedAt(Date.now())}
        />
      )}
      {faPickup && league && (
        <PickupModal
          league={league}
          pid={faPickup.player_id}
          name={faPickup.name}
          pos={faPickup.pos}
          team={faPickup.team}
          onClose={() => setFaPickup(null)}
          onDone={(pid) => {
            api.notifyClaimsChanged(league.league_id, pid);
            setFaPickup(null);
            setUpdatedAt(Date.now());
            void refreshLeague(league.league_id, { verify: false });
          }}
        />
      )}
      {checkOpen && (
        <SystemCheck
          onClose={() => setCheckOpen(false)}
          onShowSetup={() => { setCheckOpen(false); setSetupOpen(true); }}
        />
      )}
      {/* "ver los pasos" de quien ya está conectado: el mismo tutorial, en
          modo referencia (se cierra sin tocar nada). */}
      {setupOpen && (
        <div className="modal-backdrop" onClick={() => setSetupOpen(false)}>
          <Onboarding onDone={() => setSetupOpen(false)} onManual={() => { setSetupOpen(false); setAuthOpen(true); }} />
        </div>
      )}
      {authOpen && (
        <AuthModal
          onClose={() => setAuthOpen(false)}
          onSaved={() => reloadFresh()}
        />
      )}
      <footer><small className="muted">be {beVer || '?'}</small></footer>
    </>
  );
}
