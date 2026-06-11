import type { ChartRangeKey, DashboardStatus, MarketChart, SettingsState, TradeJournalPage } from "./types";

async function readJsonResponse<T extends { error?: string }>(response: Response): Promise<T> {
  const text = await response.text();
  let data: T;
  try {
    data = JSON.parse(text || "{}") as T;
  } catch (error) {
    const preview = text.trim().slice(0, 80);
    throw new Error(
      `后端接口返回了非 JSON 内容，请确认 Python Web 后端正在运行，并且 /api 代理指向 http://127.0.0.1:8787。返回片段：${preview}`,
    );
  }
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

export async function fetchStatus(): Promise<DashboardStatus> {
  const response = await fetch("/api/status", { cache: "no-store" });
  return readJsonResponse<DashboardStatus>(response);
}

export async function postAction(path: string, payload: SettingsState & { live_confirmed?: boolean }): Promise<DashboardStatus> {
  const response = await fetch(path, {
    method: "POST",
    headers: dashboardHeaders(payload),
    body: JSON.stringify(payload),
  });
  return readJsonResponse<DashboardStatus>(response);
}

export async function postPositionClose(payload: SettingsState & { symbol: string; close_quantity: string; live_confirmed?: boolean }): Promise<DashboardStatus> {
  const response = await fetch("/api/close-position", {
    method: "POST",
    headers: dashboardHeaders(payload),
    body: JSON.stringify(payload),
  });
  return readJsonResponse<DashboardStatus>(response);
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
  return readJsonResponse<MarketChart>(response);
}

export async function fetchTrades(view: "round_trips" | "events", limit: number, offset: number): Promise<TradeJournalPage> {
  const params = new URLSearchParams({ view, limit: String(limit), offset: String(offset) });
  const response = await fetch(`/api/trades?${params.toString()}`, { cache: "no-store" });
  return readJsonResponse<TradeJournalPage>(response);
}
