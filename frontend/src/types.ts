export type Page =
  | "home"
  | "explore"
  | "compare"
  | "patterns"
  | "data"
  | "settings";
export type Metric = "aircraft" | "sessions" | "observations";
export interface Status {
  version: string;
  demo: boolean;
  source: {
    connected: boolean;
    path: string;
    error?: string;
    first_seen: number | null;
    last_seen: number | null;
    observations: number;
    aircraft: number;
    bytes: number;
  };
  timezone: string;
  session_gap_minutes: number;
}
export interface Service {
  id: string;
  label: string;
  description: string;
  url: string;
  icon: string;
}
export interface Settings {
  services: Service[];
  theme: "dark" | "light" | "system";
  refresh_seconds: number;
  session_gap_minutes: number;
}
export interface Filters {
  metric: Metric;
  preset: string;
  duration: string;
  unit: string;
  start: string;
  end: string;
  group: string;
  stat: string;
  category: string;
  aircraft_type: string;
  operator: string;
  icao: string;
  callsign: string;
  month: string;
  exclude_partial: boolean;
  session_gap: number;
  fold?: "earlier" | "later";
}
export interface Series {
  key: string;
  label: string;
  start: number;
  end: number;
  value: number | null;
  count: number;
  coverage_pct: number;
  samples: number;
}
export interface Breakdown {
  name: string;
  value: number;
}
export interface Analytics {
  metric: Metric;
  group: string;
  stat: string;
  start: number;
  end: number;
  total: number;
  summary: {
    aircraft: number;
    sessions: number;
    observations: number;
    peak_concurrent: number | null;
    operator_known_pct: number;
  };
  series: Series[];
  breakdowns: {
    types: Breakdown[];
    operators: Breakdown[];
    categories: Breakdown[];
  };
  heatmap: {
    day: number;
    hour: number;
    value: number | null;
    samples: number;
  }[];
  coverage: {
    pct: number;
    active_seconds: number;
    requested_seconds: number;
    method: string;
    warnings: string[];
  };
  warnings: string[];
}
export interface Comparison {
  a: Analytics;
  b: Analytics;
  delta: number | null;
  percent_change: number | null;
  comparable: boolean;
  warnings: string[];
}
export interface UpdateStatus {
  installed_version: string;
  available_version: string | null;
  status: string;
  progress: number;
  message: string;
  history: Record<string, unknown>[];
  release_notes: string;
  configured: boolean;
  can_rollback: boolean;
}
export interface Table {
  name: string;
  columns: { name: string; type: string }[];
  technical: boolean;
}
export interface Rows {
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
}
