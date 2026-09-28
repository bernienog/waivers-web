export interface ResolveRules {
  waiver_day_of_week?: number;
  waiver_clear_days?: number;
  daily_waivers?: number;
  leg?: number;
}

export interface League {
  league_id: string;
  name: string;
  /** Posiciones que la liga rosteriza (regla de la liga: define los
   * filtros y si K/DEF aplican). */
  roster_positions?: string[];
  mode: 'faab' | 'claim' | string;
  waiver_type: number;
  budget_left: number | null;
  bid_min: number;
  roster_id?: number;
  waiver_priority?: number | null;
  record?: string;
  players_count?: number;
  resolve?: ResolveRules;
  fetched_at?: number;
  roster_space?: {
    capacity: number;
    used: number;
    free: number;
    taxi_capacity: number;
    taxi_used: number;
    taxi_free: number;
    reserve_capacity: number;
    reserve_used: number;
    reserve_free: number;
  };
}

export interface WatchItem {
  player_id: string;
  base_bid: number;
  priority: number;
  notes: string;
  name?: string;
  pos?: string;
  team?: string;
}

export interface PendingTx {
  transaction_id: string;
  leg: number;
  adds: Record<string, number> | null;
  drops: Record<string, number> | null;
  settings: Record<string, number> | null;
}
