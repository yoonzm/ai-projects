const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text || `HTTP ${response.status}`;
    try {
      const payload = JSON.parse(text);
      message = payload.detail || message;
    } catch {
      // Keep the raw response text for non-JSON errors.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  listCases: () => request<any[]>("/api/cases"),
  getCase: (caseId: string) => request<any>(`/api/cases/${caseId}`),
  createCase: (message: string, scenario: string) =>
    request<any>("/api/cases", {
      method: "POST",
      body: JSON.stringify({ message, scenario }),
    }),
  continueCase: (caseId: string, message: string) =>
    request<any>(`/api/cases/${caseId}/message`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  approveCase: (caseId: string, payload: any) =>
    request<any>(`/api/cases/${caseId}/approve`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listModelConfigs: () => request<any[]>("/api/model-configs"),
  createModelConfig: (payload: any) =>
    request<any>("/api/model-configs", { method: "POST", body: JSON.stringify(payload) }),
  activateModelConfig: (configId: string) =>
    request<any>(`/api/model-configs/${configId}/activate`, { method: "POST" }),
  testModelConfig: (configId: string) =>
    request<any>(`/api/model-configs/${configId}/test`, { method: "POST" }),
  deleteModelConfig: (configId: string) =>
    request<any>(`/api/model-configs/${configId}`, { method: "DELETE" }),
};
