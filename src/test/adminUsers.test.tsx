import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { AdminRoute } from '../components/auth/AdminRoute';
import { AppLayout } from '../components/layout/AppLayout';
import { AdminUsersPage } from '../pages/AdminUsersPage';
import { authApi } from '../services/authApi';
import { adminUsersApi } from '../services/adminUsersApi';
import { observatoryApi } from '../services/observatoryApi';

describe('Administrative Users & Role-Based Access Control (Bloque 11A)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(observatoryApi, 'isUsingMock').mockReturnValue(false);
  });

  it('does NOT display "Usuarios" link in navigation for standard user (role="user")', async () => {
    vi.spyOn(authApi, 'getMe').mockResolvedValue({
      id: 'usr-regular-1',
      email: 'regular@example.com',
      display_name: 'Regular User',
      role: 'user',
      is_active: true,
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider>
          <AppLayout>
            <div>Dashboard Content</div>
          </AppLayout>
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Regular User')).toBeInTheDocument();
    });

    expect(screen.queryByRole('link', { name: /usuarios/i })).not.toBeInTheDocument();
    expect(screen.queryByText('ADMIN')).not.toBeInTheDocument();
  });

  it('displays "Usuarios" link in navigation and ADMIN badge for administrator (role="admin")', async () => {
    vi.spyOn(authApi, 'getMe').mockResolvedValue({
      id: 'usr-admin-1',
      email: 'dani@doobot.ai',
      display_name: 'Daniel Gonzalez',
      role: 'admin',
      is_active: true,
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider>
          <AppLayout>
            <div>Dashboard Content</div>
          </AppLayout>
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Daniel Gonzalez')).toBeInTheDocument();
    });

    expect(screen.getByRole('link', { name: /usuarios/i })).toBeInTheDocument();
    expect(screen.getByText('ADMIN')).toBeInTheDocument();
  });

  it('blocks standard user (role="user") from accessing AdminRoute with 403 Forbidden', async () => {
    vi.spyOn(authApi, 'getMe').mockResolvedValue({
      id: 'usr-regular-2',
      email: 'unauthorized@example.com',
      display_name: 'Unauthorized User',
      role: 'user',
      is_active: true,
    });

    render(
      <MemoryRouter initialEntries={['/admin/usuarios']}>
        <AuthProvider>
          <Routes>
            <Route element={<AdminRoute />}>
              <Route
                path="/admin/usuarios"
                element={
                  <AppLayout>
                    <AdminUsersPage />
                  </AppLayout>
                }
              />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/403 — Acceso Restringido/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/exclusivamente para usuarios con privilegios de administrador/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /volver al cuadro de mando/i })).toBeInTheDocument();
  });

  it('allows administrator (role="admin") to view AdminUsersPage and user catalog', async () => {
    vi.spyOn(authApi, 'getMe').mockResolvedValue({
      id: 'usr-admin-1',
      email: 'dani@doobot.ai',
      display_name: 'Daniel Gonzalez',
      role: 'admin',
      is_active: true,
    });

    vi.spyOn(adminUsersApi, 'listUsers').mockResolvedValue([
      {
        id: 'usr-admin-1',
        email: 'dani@doobot.ai',
        display_name: 'Daniel Gonzalez',
        role: 'admin',
        is_active: true,
        created_at: '2026-09-01T10:00:00Z',
        last_login_at: '2026-09-10T12:00:00Z',
      },
      {
        id: 'usr-client-2',
        email: 'cliente@hitchings.local',
        display_name: 'Cliente Demo',
        role: 'user',
        is_active: true,
        created_at: '2026-09-05T14:30:00Z',
        last_login_at: null,
      },
    ]);

    render(
      <MemoryRouter initialEntries={['/admin/usuarios']}>
        <AuthProvider>
          <Routes>
            <Route element={<AdminRoute />}>
              <Route
                path="/admin/usuarios"
                element={
                  <AppLayout>
                    <AdminUsersPage />
                  </AppLayout>
                }
              />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Gestión de Usuarios')).toBeInTheDocument();
      expect(screen.getByText('cliente@hitchings.local')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /crear usuario/i })).toBeInTheDocument();
  });

  it('opens and cancels user creation modal', async () => {
    vi.spyOn(authApi, 'getMe').mockResolvedValue({
      id: 'usr-admin-1',
      email: 'dani@doobot.ai',
      display_name: 'Daniel Gonzalez',
      role: 'admin',
      is_active: true,
    });

    vi.spyOn(adminUsersApi, 'listUsers').mockResolvedValue([]);

    render(
      <MemoryRouter initialEntries={['/admin/usuarios']}>
        <AuthProvider>
          <AdminUsersPage />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /crear usuario/i })).toBeInTheDocument();
    });

    // Open create modal
    fireEvent.click(screen.getByRole('button', { name: /crear usuario/i }));

    expect(screen.getByText('Crear Nuevo Usuario')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/mínimo 12 caracteres/i)).toBeInTheDocument();

    // Click cancel
    fireEvent.click(screen.getByRole('button', { name: /cancelar/i }));
    expect(screen.queryByText('Crear Nuevo Usuario')).not.toBeInTheDocument();
  });
});
