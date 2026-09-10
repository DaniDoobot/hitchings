import { AuthUser, LoginCredentials } from '../types/auth';

export class AuthApiService {
  private baseUrl: string;

  constructor(customBaseUrl?: string) {
    const isProd = typeof import.meta !== 'undefined' && import.meta.env?.PROD === true;
    const envBaseUrl =
      typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL !== undefined
        ? import.meta.env.VITE_API_BASE_URL
        : undefined;

    let rawBaseUrl: string;
    if (customBaseUrl !== undefined) {
      rawBaseUrl = customBaseUrl;
    } else if (envBaseUrl !== undefined) {
      rawBaseUrl = envBaseUrl;
    } else if (isProd) {
      rawBaseUrl = '';
    } else {
      rawBaseUrl = 'http://127.0.0.1:8000';
    }
    this.baseUrl = rawBaseUrl.trim().replace(/\/$/, '');
  }

  public getBaseUrl(): string {
    return this.baseUrl;
  }

  public async login(credentials: LoginCredentials): Promise<AuthUser> {
    const res = await fetch(`${this.baseUrl}/api/v1/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify(credentials),
    });

    if (!res.ok) {
      let message = 'Credenciales incorrectas.';
      try {
        const errJson = await res.json();
        if (errJson?.detail) message = errJson.detail;
      } catch {
        // use fallback message
      }
      throw new Error(message);
    }

    const data = await res.json();
    return data.user;
  }

  public async getMe(): Promise<AuthUser> {
    const res = await fetch(`${this.baseUrl}/api/v1/auth/me`, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
      },
      credentials: 'include',
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: No autenticado`);
    }

    return await res.json();
  }

  public async logout(): Promise<void> {
    await fetch(`${this.baseUrl}/api/v1/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    });
  }
}

export const authApi = new AuthApiService();
