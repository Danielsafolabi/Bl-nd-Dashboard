export type State = "IDLE" | "FALLING" | "LAYING" | "ALERT";

export interface Device {
  id: string;
  name: string;
  current_state: State | null;
  last_seen: number | null;
  online: boolean;
  open_alert: boolean;
}
export interface EventRow { id: string; device_id: string; device_name: string; state: State; prev_state: State | null; ts: number; }
export interface AlertRow {
  id: string; device_id: string; device_name: string; ts: number;
  acked_at: number | null; acked_by: string | null;
}
export interface StatsBucket { hour_start: number; falls: number; alerts: number; }
export interface Privacy {
  camera_derived_fields: string[];
  accepted_device_payload: string[];
  max_device_frame_bytes: number;
  retention_days: number;
  stores_images: boolean;
  enforced_by: string;
  schema: { collection: string; fields: string[] }[];
}

export class AuthError extends Error {}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (r.status === 401) throw new AuthError((await r.json().catch(() => ({}))).detail ?? "unauthorized");
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  me: () => req<{ id: string; username: string }>("/api/auth/me"),
  login: (username: string, password: string) =>
    req<{ username: string }>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => req("/api/auth/logout", { method: "POST" }),
  devices: () => req<{ devices: Device[]; offline_after_s: number }>("/api/devices"),
  events: (deviceId?: string) =>
    req<{ events: EventRow[] }>(`/api/events?limit=200${deviceId ? `&device_id=${encodeURIComponent(deviceId)}` : ""}`),
  alerts: () => req<{ alerts: AlertRow[] }>("/api/alerts?limit=100"),
  ack: (id: string) => req(`/api/alerts/${id}/ack`, { method: "POST" }),
  stats: (hours = 24) => req<{ buckets: StatsBucket[] }>(`/api/stats?hours=${hours}`),
  privacy: () => req<Privacy>("/api/privacy"),
};

export function ago(ts: number | null): string {
  if (ts == null) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}
