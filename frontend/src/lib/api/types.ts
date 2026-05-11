/**
 * TypeScript interfaces matching the csauto FastAPI response shapes.
 *
 * These are kept in sync with the Pydantic models defined in
 * csauto/fastapi_routes/common.py and the individual route modules.
 */

/* Status & hero */

export interface StatusRow {
  case_id: string;
  status: string | null;
  convergence: string | null;
  note: string | null;
  nprocs: number | null;
  nt: number | null;
  last_iter: number | null;
  duration_s: number | null;
  duration: string | null;
  last_mod: string | null;
  resu_size_mb: number | null;
  doe: Record<string, string | number> | null;
  [extra: string]: unknown;
}

export interface StatusPayload {
  rows: StatusRow[];
  doe_columns: string[];
}

/* Performance */

export interface PerfRecord {
  case_id: string;
  elapsed_time: string | null;
  mpi_ranks: string | null;
  threads: string | null;
  io_time: string | null;
  linear_solver_time: string | null;
  gradients_time: string | null;
  balances_time: string | null;
}

export interface PerfPayload {
  records: PerfRecord[];
}

/* Shared */

export interface StringListResponse {
  columns?: string[];
  dirs?: string[];
  files?: string[];
}

/* Errors */

export interface ErrorItem {
  case_id: string;
  file: string;
  severity: string;
  line_html: string;
  tail_index: number | null;
  tail_total: number | null;
}

export interface ErrorsPayload {
  items: ErrorItem[];
}

/* Restart origins */

export interface RestartOriginEntry {
  iteration?: number;
  time?: number;
  [key: string]: number | undefined;
}

export interface RestartOriginResponse {
  origins: Record<string, RestartOriginEntry>;
}

/* Probe position */

export interface ProbePositionResponse {
  found: boolean;
  x?: number;
  y?: number;
  z?: number;
  [key: string]: unknown;
}

/* Cleanup result */

export interface CleanupResponse {
  resu_removed: number;
  logs_truncated: number;
  bytes_freed: number;
  cid_removed: number;
  pycache_removed: number;
}

/* Action params (sent by frontend) */

export interface RunParams {
  n: number;
  nt: number;
  maxParallel: number | null;
}

export interface RestartParams extends RunParams {
  restartMode: "iterations" | "physical_time";
  restartValue: number;
}

export interface CleanChoice {
  action: "keep_latest" | "delete_all" | "keep_folder" | "delete_folder";
  keepLast?: number;
  keepResu?: string[];
  deleteResu?: string[];
}

/* QoI campaign table — /api/qoi/results */

export interface QoIRecipeSummary {
  name: string;
  type: string;
}

export interface QoIResultRow {
  case_id: string;
  doe: Record<string, string>;
  qois: Record<string, number | null>;
  status: "ok" | "error";
  errors: string[];
}

export interface QoIResultsPayload {
  recipes: QoIRecipeSummary[];
  doe_columns: string[];
  qoi_columns: string[];
  rows: QoIResultRow[];
  n_total: number;
  n_errors: number;
  mode: "managed" | "injected";
}
