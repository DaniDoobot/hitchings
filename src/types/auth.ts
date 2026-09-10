export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: 'admin' | 'user';
  is_active?: boolean;
}

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated';

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface AuthContextValue {
  user: AuthUser | null;
  status: AuthStatus;
  login: (credentials: LoginCredentials) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: 'admin' | 'user';
  is_active: boolean;
  created_at: string;
  last_login_at?: string | null;
}

export interface CreateUserPayload {
  email: string;
  display_name: string;
  role: 'admin' | 'user';
  password: string;
}

export interface UpdateUserPayload {
  email?: string;
  display_name?: string;
  role?: 'admin' | 'user';
  is_active?: boolean;
}

export interface ResetPasswordPayload {
  password: string;
}

