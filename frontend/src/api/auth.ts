import apiClient from './client';

export interface AuthResponse {
  access_token: string;
  token_type: string;
  username: string;
  expires_in_minutes: number;
}

export interface MeResponse {
  username: string;
  role_id: string;
  role_name: string;
  scope_ids: string[];
}

export async function login(username: string, password: string): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/auth/login', { username, password });
  return response.data;
}

export async function register(username: string, password: string): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/auth/register', { username, password });
  return response.data;
}

export async function getMe(): Promise<MeResponse> {
  const response = await apiClient.get<MeResponse>('/auth/me');
  return response.data;
}
