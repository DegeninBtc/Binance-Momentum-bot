import type { DashboardStatus, SettingsState } from "./types";

export async function fetchStatus(): Promise<DashboardStatus> {
  const response = await fetch("/api/status", { cache: "no-store" });
  const data = (await response.json()) as DashboardStatus;
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

export async function postAction(path: string, payload: SettingsState): Promise<DashboardStatus> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as DashboardStatus;
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}
