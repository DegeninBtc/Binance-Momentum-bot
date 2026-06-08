import type { ChartRangeKey, DashboardStatus, MarketChart, SettingsState } from "./types";

export async function fetchStatus(): Promise<DashboardStatus> {
  const response = await fetch("/api/status", { cache: "no-store" });
  const data = (await response.json()) as DashboardStatus;
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

export async function postAction(path: string, payload: SettingsState & { live_confirmed?: boolean }): Promise<DashboardStatus> {
  const response = await fetch(path, {
    method: "POST",
    headers: dashboardHeaders(payload),
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as DashboardStatus;
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

export async function postPositionClose(payload: SettingsState & { symbol: string; close_quantity: string; live_confirmed?: boolean }): Promise<DashboardStatus> {
  const response = await fetch("/api/close-position", {
    method: "POST",
    headers: dashboardHeaders(payload),
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as DashboardStatus;
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

export function dashboardHeaders(payload: Pick<SettingsState, "dashboard_auth_token">): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = String(payload.dashboard_auth_token || "").trim();
  if (token) {
    headers["X-Dashboard-Token"] = token;
  }
  return headers;
}

export async function fetchMarketChart(symbol: string, range: ChartRangeKey, testnet: boolean): Promise<MarketChart> {
  const params = new URLSearchParams({ symbol, range, testnet: String(testnet) });
  const response = await fetch(`/api/market-chart?${params.toString()}`, { cache: "no-store" });
  const data = (await response.json()) as MarketChart;
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}
