<!--
  QoiCard — campaign-level QoI table.

  Calls GET /api/qoi/results and renders the assembled DOE x QoI table.
  Sortable columns, status filter, CSV export. Empty/error states surface
  recipe configuration issues explicitly.
-->
<script lang="ts">
  import { onDestroy } from "svelte";
  import Button from "$lib/components/shared/Button.svelte";
  import CardShell from "$lib/components/shared/CardShell.svelte";
  import AutoRefreshToggle from "$lib/components/shared/AutoRefreshToggle.svelte";
  import Dropdown from "$lib/components/shared/Dropdown.svelte";
  import FieldRow from "$lib/components/shared/FieldRow.svelte";
  import FormLabel from "$lib/components/shared/FormLabel.svelte";
  import Icon from "$lib/components/shared/Icon.svelte";
  import { fetchQoIResults } from "$lib/api/endpoints";
  import { saveCsvBlob, buildPlotFilename } from "$lib/actions/export";
  import type { QoIResultsPayload, QoIResultRow } from "$lib/api/types";
  import {
    RefreshCw,
    Download,
    ArrowUp,
    ArrowDown,
    CircleAlert,
  } from "lucide-svelte";
  import {
    startTimer,
    stopTimer,
    getAutoRefreshEnabled,
    setAutoRefreshEnabled,
    onGlobalRefresh,
  } from "$lib/stores/refresh.svelte";

  const QOI_REFRESH_MS = 10000;

  let payload = $state<QoIResultsPayload | null>(null);
  let loading = $state(false);
  let fetchError = $state("");
  let autoRefresh = $state(getAutoRefreshEnabled("qoi"));
  $effect(() => {
    setAutoRefreshEnabled("qoi", autoRefresh);
  });

  let statusFilter = $state<"all" | "ok" | "error">("all");
  let sortKey = $state<string>("case_id");
  let sortDir = $state<"asc" | "desc">("asc");

  async function loadResults(): Promise<void> {
    loading = true;
    try {
      payload = await fetchQoIResults();
      fetchError = "";
    } catch (err) {
      fetchError = err instanceof Error ? err.message : "Failed to load QoI results";
    } finally {
      loading = false;
    }
  }

  void loadResults();

  $effect(() => {
    if (autoRefresh) {
      startTimer("qoi", loadResults, QOI_REFRESH_MS);
    } else {
      stopTimer("qoi");
    }
  });

  const unsubGlobal = onGlobalRefresh(() => {
    void loadResults();
  });

  onDestroy(() => {
    stopTimer("qoi");
    unsubGlobal();
  });

  const STATUS_OPTIONS = [
    { value: "all", label: "All" },
    { value: "ok", label: "OK" },
    { value: "error", label: "Errors" },
  ];

  function toggleSort(key: string): void {
    if (sortKey === key) {
      sortDir = sortDir === "asc" ? "desc" : "asc";
    } else {
      sortKey = key;
      sortDir = "asc";
    }
  }

  function rowValue(row: QoIResultRow, key: string): string | number | null {
    if (key === "case_id") return row.case_id;
    if (key === "_status") return row.status;
    if (payload?.doe_columns.includes(key)) return row.doe[key] ?? "";
    if (payload?.qoi_columns.includes(key)) return row.qois[key] ?? null;
    return "";
  }

  function compare(a: string | number | null, b: string | number | null): number {
    if (a === null || a === undefined) return b === null ? 0 : 1;
    if (b === null || b === undefined) return -1;
    if (typeof a === "number" && typeof b === "number") return a - b;
    const sa = String(a);
    const sb = String(b);
    const na = Number(sa);
    const nb = Number(sb);
    if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
    return sa.localeCompare(sb);
  }

  let filteredRows = $derived.by<QoIResultRow[]>(() => {
    if (!payload) return [];
    const rows = payload.rows;
    if (statusFilter === "all") return rows;
    return rows.filter((r) => r.status === statusFilter);
  });

  let sortedRows = $derived.by<QoIResultRow[]>(() => {
    const rows = [...filteredRows];
    rows.sort((a, b) => {
      const va = rowValue(a, sortKey);
      const vb = rowValue(b, sortKey);
      const cmp = compare(va, vb);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  });

  function formatQoI(value: number | null): string {
    if (value === null || value === undefined) return "—";
    if (Math.abs(value) >= 1e4 || (Math.abs(value) > 0 && Math.abs(value) < 1e-3)) {
      return value.toExponential(3);
    }
    return value.toPrecision(5);
  }

  function exportCsv(): void {
    if (!payload || payload.rows.length === 0) return;
    const cols = [
      "case_id",
      ...payload.doe_columns,
      ...payload.qoi_columns,
      "_status",
      "_errors",
    ];
    const header = cols.join(",");
    const lines = sortedRows.map((r) => {
      const cells = cols.map((c) => {
        if (c === "case_id") return r.case_id;
        if (c === "_status") return r.status;
        if (c === "_errors") return `"${r.errors.join("; ").replace(/"/g, '""')}"`;
        if (payload!.doe_columns.includes(c)) return r.doe[c] ?? "";
        if (payload!.qoi_columns.includes(c)) {
          const v = r.qois[c];
          return v === null || v === undefined ? "" : String(v);
        }
        return "";
      });
      return cells.join(",");
    });
    const csv = [header, ...lines].join("\n");
    const filename = buildPlotFilename("qoi_results", [], "csv");
    void saveCsvBlob(csv, filename);
  }
</script>

<div class="col-span-12">
  <CardShell eyebrow="Diagnostics" title="Quantities of Interest" wide>
    {#snippet actions()}
      <AutoRefreshToggle
        name="qoi"
        intervalMs={QOI_REFRESH_MS}
        bind:checked={autoRefresh}
        onRefresh={loadResults}
      />
      {#if !autoRefresh}
        <Button variant="primary" onclick={loadResults}>
          <Icon icon={RefreshCw} /> Refresh
        </Button>
      {/if}
    {/snippet}

    {#if !payload && loading}
      <p class="text-xs text-muted">Loading…</p>
    {:else if fetchError}
      <p class="text-xs text-edf-orange-fonce">{fetchError}</p>
    {:else if payload && payload.recipes.length === 0}
      <p class="text-xs text-muted">
        No <code>[[qoi]]</code> recipes configured in <code>csauto.toml</code>.
        Add one (e.g. <code>force_coefficient</code> or <code>pressure_drop</code>)
        and re-run <code>csauto prepare</code> to enable QoI extraction.
      </p>
    {:else if payload}
      <FieldRow>
        <FormLabel text="Status">
          <Dropdown
            class="w-[100px]"
            options={STATUS_OPTIONS}
            value={statusFilter}
            onchange={(v) => (statusFilter = v as "all" | "ok" | "error")}
          />
        </FormLabel>
        <div class="self-end ml-auto flex items-center gap-2">
          <span class="text-xs text-muted">
            mode: <strong>{payload.mode}</strong>
            · {payload.recipes.length} recipe{payload.recipes.length === 1 ? "" : "s"}
            · {payload.n_total} case{payload.n_total === 1 ? "" : "s"}
            {#if payload.n_errors > 0}
              · <span class="text-edf-orange-fonce font-bold">
                {payload.n_errors} error{payload.n_errors === 1 ? "" : "s"}
              </span>
            {/if}
          </span>
          <Button
            variant="secondary"
            size="sm"
            onclick={exportCsv}
            disabled={payload.rows.length === 0}
          >
            <Icon icon={Download} /> Download CSV
          </Button>
        </div>
      </FieldRow>

      {#if sortedRows.length === 0}
        <p class="text-xs text-muted mt-2">
          No rows to display (filter:&nbsp;<strong>{statusFilter}</strong>).
        </p>
      {:else}
        <div class="overflow-auto mt-2 max-h-[420px]">
          <table class="qoi-table">
            <thead>
              <tr>
                {#each ["case_id", ...payload.doe_columns, ...payload.qoi_columns, "_status"] as col (col)}
                  <th>
                    <button
                      class="qoi-th-btn"
                      type="button"
                      onclick={() => toggleSort(col)}
                    >
                      <span>{col === "_status" ? "status" : col}</span>
                      {#if sortKey === col}
                        <Icon
                          icon={sortDir === "asc" ? ArrowUp : ArrowDown}
                          size={11}
                        />
                      {/if}
                    </button>
                  </th>
                {/each}
              </tr>
            </thead>
            <tbody>
              {#each sortedRows as row (row.case_id)}
                <tr class={row.status === "error" ? "qoi-row-err" : ""}>
                  <td>{row.case_id}</td>
                  {#each payload.doe_columns as col (col)}
                    <td>{row.doe[col] ?? ""}</td>
                  {/each}
                  {#each payload.qoi_columns as col (col)}
                    <td class="qoi-num">{formatQoI(row.qois[col])}</td>
                  {/each}
                  <td>
                    {#if row.status === "ok"}
                      <span class="qoi-status-ok">ok</span>
                    {:else}
                      <span class="qoi-status-err" title={row.errors.join("\n")}>
                        <Icon icon={CircleAlert} size={11} /> error
                      </span>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}
  </CardShell>
</div>

<style>
  .qoi-table {
    border-collapse: collapse;
    font-size: 12px;
    width: 100%;
  }
  .qoi-table th,
  .qoi-table td {
    border-bottom: 1px solid rgba(0, 0, 0, 0.08);
    padding: 4px 8px;
    white-space: nowrap;
  }
  .qoi-table thead th {
    position: sticky;
    top: 0;
    background: var(--color-bg, #fff);
    text-align: left;
    font-weight: 700;
    font-size: 11px;
    color: var(--color-edf-bleu-fonce, #1057c8);
  }
  .qoi-th-btn {
    background: transparent;
    border: none;
    padding: 0;
    font: inherit;
    color: inherit;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .qoi-th-btn:hover {
    text-decoration: underline;
  }
  .qoi-num {
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .qoi-row-err {
    background: rgba(214, 67, 10, 0.04);
  }
  .qoi-status-ok {
    color: #19a974;
    font-weight: 700;
    font-size: 11px;
  }
  .qoi-status-err {
    color: var(--color-edf-orange-fonce, #d6430a);
    font-weight: 700;
    font-size: 11px;
    display: inline-flex;
    align-items: center;
    gap: 3px;
    cursor: help;
  }
</style>
