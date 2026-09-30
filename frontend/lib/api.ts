// All backend calls go through here. The browser calls same-origin /api/*: Caddy (local, demo)
// or the load balancer (prod) routes it to the backend, so there is no CORS and no API URL in the bundle.
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} /api${path} failed: ${response.status}`);
  return response.json() as Promise<T>;
}
