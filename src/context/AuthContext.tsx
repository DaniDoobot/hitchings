import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { AuthUser, AuthStatus, LoginCredentials, AuthContextValue } from '../types/auth';
import { authApi } from '../services/authApi';
import { observatoryApi } from '../services/observatoryApi';

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');

  const isMock = observatoryApi.isUsingMock();

  const refresh = useCallback(async () => {
    if (isMock) {
      setUser({
        id: 'dev-mock-user-001',
        email: 'cliente@hitchings.local',
        display_name: 'Cliente Demo (DEV)',
        role: 'admin',
        is_active: true,
      });
      setStatus('authenticated');
      return;
    }

    try {
      const currentUser = await authApi.getMe();
      setUser(currentUser);
      setStatus('authenticated');
    } catch {
      setUser(null);
      setStatus('unauthenticated');
    }
  }, [isMock]);

  useEffect(() => {
    refresh();

    const handleUnauthorized = () => {
      setUser(null);
      setStatus('unauthenticated');
    };

    window.addEventListener('hitchings:unauthorized', handleUnauthorized);
    return () => {
      window.removeEventListener('hitchings:unauthorized', handleUnauthorized);
    };
  }, [refresh]);

  const login = useCallback(async (credentials: LoginCredentials) => {
    if (isMock) {
      setUser({
        id: 'dev-mock-user-001',
        email: credentials.email || 'cliente@hitchings.local',
        display_name: 'Cliente Demo (DEV)',
        role: 'admin',
        is_active: true,
      });
      setStatus('authenticated');
      return;
    }

    const authenticatedUser = await authApi.login(credentials);
    setUser(authenticatedUser);
    setStatus('authenticated');
  }, [isMock]);

  const logout = useCallback(async () => {
    if (!isMock) {
      try {
        await authApi.logout();
      } catch (err) {
        console.error('Error during logout API call:', err);
      }
    }
    setUser(null);
    setStatus('unauthenticated');
  }, [isMock]);

  const value: AuthContextValue = {
    user,
    status,
    login,
    logout,
    refresh,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
