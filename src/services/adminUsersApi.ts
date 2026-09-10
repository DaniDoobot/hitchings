import { AdminUser, CreateUserPayload, UpdateUserPayload, ResetPasswordPayload } from '../types/auth';
import { observatoryApi } from './observatoryApi';

const DEV_MOCK_USERS: AdminUser[] = [
  {
    id: 'mock-admin-001',
    email: 'dani@doobot.ai',
    display_name: 'Daniel (Administrador)',
    role: 'admin',
    is_active: true,
    created_at: '2026-09-01T10:00:00Z',
    last_login_at: '2026-09-10T12:00:00Z',
  },
  {
    id: 'mock-user-002',
    email: 'cliente@hitchings.local',
    display_name: 'Cliente Demo (DEV)',
    role: 'user',
    is_active: true,
    created_at: '2026-09-05T14:30:00Z',
    last_login_at: '2026-09-09T18:15:00Z',
  },
  {
    id: 'mock-user-003',
    email: 'inactivo@ejemplo.com',
    display_name: 'Usuario Inactivo',
    role: 'user',
    is_active: false,
    created_at: '2026-08-20T09:00:00Z',
    last_login_at: null,
  },
];

export class AdminUsersApiService {
  private baseUrl: string;
  private mockUsers: AdminUser[] = [...DEV_MOCK_USERS];

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

  public async listUsers(): Promise<AdminUser[]> {
    if (observatoryApi.isUsingMock()) {
      return [...this.mockUsers];
    }

    const res = await fetch(`${this.baseUrl}/api/v1/admin/users`, {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
      credentials: 'include',
    });

    if (!res.ok) {
      const errorMsg = await this.extractErrorMessage(res, 'Error al listar usuarios');
      throw new Error(errorMsg);
    }

    return await res.json();
  }

  public async createUser(payload: CreateUserPayload): Promise<AdminUser> {
    if (observatoryApi.isUsingMock()) {
      const exists = this.mockUsers.some(u => u.email === payload.email.trim().toLowerCase());
      if (exists) {
        throw new Error(`Ya existe un usuario registrado con el correo electrónico '${payload.email}'.`);
      }
      const newUser: AdminUser = {
        id: `mock-user-${Date.now()}`,
        email: payload.email.trim().toLowerCase(),
        display_name: payload.display_name.trim(),
        role: payload.role,
        is_active: true,
        created_at: new Date().toISOString(),
        last_login_at: null,
      };
      this.mockUsers.unshift(newUser);
      return newUser;
    }

    const res = await fetch(`${this.baseUrl}/api/v1/admin/users`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const errorMsg = await this.extractErrorMessage(res, 'Error al crear usuario');
      throw new Error(errorMsg);
    }

    return await res.json();
  }

  public async updateUser(userId: string, payload: UpdateUserPayload): Promise<AdminUser> {
    if (observatoryApi.isUsingMock()) {
      const index = this.mockUsers.findIndex(u => u.id === userId);
      if (index === -1) {
        throw new Error('Usuario no encontrado.');
      }
      const existing = this.mockUsers[index];
      const updated: AdminUser = {
        ...existing,
        email: payload.email !== undefined ? payload.email.trim().toLowerCase() : existing.email,
        display_name: payload.display_name !== undefined ? payload.display_name.trim() : existing.display_name,
        role: payload.role !== undefined ? payload.role : existing.role,
        is_active: payload.is_active !== undefined ? payload.is_active : existing.is_active,
      };
      this.mockUsers[index] = updated;
      return updated;
    }

    const res = await fetch(`${this.baseUrl}/api/v1/admin/users/${userId}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const errorMsg = await this.extractErrorMessage(res, 'Error al actualizar usuario');
      throw new Error(errorMsg);
    }

    return await res.json();
  }

  public async resetPassword(userId: string, payload: ResetPasswordPayload): Promise<{ message: string }> {
    if (observatoryApi.isUsingMock()) {
      return { message: 'Contraseña actualizada correctamente en modo de desarrollo.' };
    }

    const res = await fetch(`${this.baseUrl}/api/v1/admin/users/${userId}/reset-password`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const errorMsg = await this.extractErrorMessage(res, 'Error al restablecer contraseña');
      throw new Error(errorMsg);
    }

    return await res.json();
  }

  private async extractErrorMessage(res: Response, fallback: string): Promise<string> {
    try {
      const data = await res.json();
      if (data?.detail) {
        if (typeof data.detail === 'string') return data.detail;
        if (Array.isArray(data.detail) && data.detail[0]?.msg) return data.detail[0].msg;
      }
      if (data?.message) return data.message;
    } catch {
      // ignore json parse error
    }
    return `${fallback} (HTTP ${res.status})`;
  }
}

export const adminUsersApi = new AdminUsersApiService();
