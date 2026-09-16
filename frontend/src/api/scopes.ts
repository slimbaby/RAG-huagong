import apiClient from './client';
import type { ScopeInfo } from '../types';

export async function getScopes(): Promise<ScopeInfo[]> {
  const response = await apiClient.get<ScopeInfo[]>('/scopes/scopes/');
  return response.data;
}
