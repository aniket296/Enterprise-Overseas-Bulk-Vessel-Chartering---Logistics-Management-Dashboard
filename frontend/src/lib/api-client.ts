import { ExecutiveDashboardSummary, UserRole } from "@/types/dashboard";

const getApiBaseUrl = (): string => {
  if (typeof window !== "undefined") {
    // In browser, use relative path so Next.js rewrites forward to Django backend
    return "/api/v1";
  }
  return process.env.INTERNAL_API_URL || "http://127.0.0.1:8000/api/v1";
};

export interface ApiFetchOptions extends RequestInit {
  role?: UserRole;
}

export async function fetchWithAuth<T>(endpoint: string, options: ApiFetchOptions = {}): Promise<T> {
  const { role = "LOGISTICS_DIRECTOR", headers = {}, ...rest } = options;
  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`;

  const requestHeaders = new Headers(headers);
  requestHeaders.set("Content-Type", "application/json");
  requestHeaders.set("Accept", "application/json");
  if (role) {
    requestHeaders.set("X-User-Role", role);
  }

  const response = await fetch(url, {
    ...rest,
    headers: requestHeaders,
    cache: "no-store",
  });

  if (!response.ok) {
    let errorDetail = `HTTP Error ${response.status}: ${response.statusText}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || errJson.error || JSON.stringify(errJson);
    } catch {
      // Fallback to status text
    }
    throw new Error(errorDetail);
  }

  return response.json() as Promise<T>;
}

export const logisticsApi = {
  getActiveFleet: (
    filters: { discharge_port?: string; cargo_type?: string; status?: string } = {},
    role: UserRole = "LOGISTICS_DIRECTOR"
  ): Promise<import("@/types/dashboard").ActiveFleetResponse> => {
    const params = new URLSearchParams();
    if (filters.discharge_port && filters.discharge_port !== "ALL") {
      params.set("discharge_port", filters.discharge_port);
    }
    if (filters.cargo_type && filters.cargo_type !== "ALL") {
      params.set("cargo_type", filters.cargo_type);
    }
    if (filters.status && filters.status !== "ALL") {
      params.set("voyage_status", filters.status);
    }
    const query = params.toString() ? `?${params.toString()}` : "";
    return fetchWithAuth(`/voyages/active/${query}`, { role });
  },

  getExecutiveSummary: (role: UserRole = "LOGISTICS_DIRECTOR"): Promise<ExecutiveDashboardSummary> => {
    return fetchWithAuth<ExecutiveDashboardSummary>("/voyages/executive-summary/", { role });
  },

  getStarvationMatrix: (role: UserRole = "LOGISTICS_DIRECTOR", lookaheadDays: number = 30) => {
    return fetchWithAuth(`/inventory/starvation-matrix/?lookahead_days=${lookaheadDays}`, { role });
  },

  getInboundForecast: (
    plantCode: string = "BSP",
    materialCategory: string = "COKING_COAL",
    horizonDays: number = 60,
    role: UserRole = "LOGISTICS_DIRECTOR"
  ) => {
    return fetchWithAuth(
      `/inventory/inbound-forecast/?plant_code=${plantCode}&material_category=${materialCategory}&horizon_days=${horizonDays}`,
      { role }
    );
  },

  simulateMitigation: (
    starvingPlant: string,
    materialCategory: string = "COKING_COAL",
    role: UserRole = "LOGISTICS_DIRECTOR"
  ) => {
    return fetchWithAuth(
      `/inventory/simulate-mitigation/?starving_plant=${starvingPlant}&material_category=${materialCategory}`,
      { role }
    );
  },

  checkHealth: async (): Promise<boolean> => {
    try {
      const baseUrl = getApiBaseUrl();
      const res = await fetch(`${baseUrl}/voyages/executive-summary/`, {
        method: "HEAD",
        headers: { "X-User-Role": "EXECUTIVE_DIRECTOR" },
        cache: "no-store",
      });
      return res.ok || res.status === 200 || res.status === 403;
    } catch {
      return false;
    }
  },
};
