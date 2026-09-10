import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { LoginPage } from '../pages/LoginPage';
import { ProtectedRoute } from '../components/auth/ProtectedRoute';
import { AuthProvider } from '../context/AuthContext';
import { authApi } from '../services/authApi';
import { observatoryApi, ObservatoryApiService } from '../services/observatoryApi';

describe('Frontend Authentication & Route Protection (Bloque 8C.1)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders login form with institutional styling and required fields', () => {
    vi.spyOn(observatoryApi, 'isUsingMock').mockReturnValue(false);
    vi.spyOn(authApi, 'getMe').mockRejectedValue(new Error('401'));

    render(
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText('HITCHINGS Y GONZALEZ')).toBeInTheDocument();
    expect(screen.getByText('Observatorio de Derecho de la Competencia')).toBeInTheDocument();
    expect(screen.getByLabelText(/correo electrónico/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/contraseña/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /iniciar sesión/i })).toBeInTheDocument();
  });

  it('handles invalid credentials with clean error message', async () => {
    vi.spyOn(observatoryApi, 'isUsingMock').mockReturnValue(false);
    vi.spyOn(authApi, 'getMe').mockRejectedValue(new Error('401'));
    vi.spyOn(authApi, 'login').mockRejectedValue(new Error('Credenciales incorrectas.'));

    render(
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>
    );

    const emailInput = screen.getByLabelText(/correo electrónico/i);
    const passwordInput = screen.getByLabelText(/contraseña/i);
    const submitBtn = screen.getByRole('button', { name: /iniciar sesión/i });

    fireEvent.change(emailInput, { target: { value: 'wrong@example.com' } });
    fireEvent.change(passwordInput, { target: { value: 'bad_password' } });
    fireEvent.click(submitBtn);

    expect(await screen.findByText('Credenciales incorrectas.')).toBeInTheDocument();
  });

  it('handles successful login and redirects to intended destination', async () => {
    vi.spyOn(observatoryApi, 'isUsingMock').mockReturnValue(false);
    vi.spyOn(authApi, 'getMe').mockRejectedValueOnce(new Error('401'));
    vi.spyOn(authApi, 'login').mockResolvedValueOnce({
      id: 'u-123',
      email: 'test@example.com',
      display_name: 'Test Client',
    });

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/login',
            state: { from: { pathname: '/observatorio', search: '?relevance_status=relevant' } },
          },
        ]}
      >
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/observatorio" element={<div>Destino Observatorio</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    const emailInput = screen.getByLabelText(/correo electrónico/i);
    const passwordInput = screen.getByLabelText(/contraseña/i);
    const submitBtn = screen.getByRole('button', { name: /iniciar sesión/i });

    fireEvent.change(emailInput, { target: { value: 'test@example.com' } });
    fireEvent.change(passwordInput, { target: { value: 'valid_password' } });
    fireEvent.click(submitBtn);

    expect(await screen.findByText('Destino Observatorio')).toBeInTheDocument();
  });

  it('redirects unauthenticated user trying to access protected route to /login', async () => {
    vi.spyOn(observatoryApi, 'isUsingMock').mockReturnValue(false);
    vi.spyOn(authApi, 'getMe').mockRejectedValue(new Error('401'));

    render(
      <MemoryRouter initialEntries={['/observatorio']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route path="/observatorio" element={<div>Contenido Protegido</div>} />
            </Route>
            <Route path="/login" element={<div>Pantalla de Login</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Pantalla de Login')).toBeInTheDocument();
    expect(screen.queryByText('Contenido Protegido')).not.toBeInTheDocument();
  });

  it('allows access to protected route when user is authenticated', async () => {
    vi.spyOn(observatoryApi, 'isUsingMock').mockReturnValue(false);
    vi.spyOn(authApi, 'getMe').mockResolvedValue({
      id: 'u-123',
      email: 'auth@example.com',
      display_name: 'Auth User',
    });

    render(
      <MemoryRouter initialEntries={['/observatorio']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route path="/observatorio" element={<div>Contenido Protegido</div>} />
            </Route>
            <Route path="/login" element={<div>Pantalla de Login</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText('Contenido Protegido')).toBeInTheDocument();
    expect(screen.queryByText('Pantalla de Login')).not.toBeInTheDocument();
  });

  it('verifies authApi always includes credentials: "include"', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ user: { id: '1', email: 'e', display_name: 'n' } }),
    } as Response);

    await authApi.login({ email: 'test@example.com', password: 'pwd' });
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/auth/login'),
      expect.objectContaining({ credentials: 'include' })
    );

    await authApi.getMe();
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/auth/me'),
      expect.objectContaining({ credentials: 'include' })
    );

    await authApi.logout();
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/auth/logout'),
      expect.objectContaining({ credentials: 'include' })
    );
  });

  it('verifies observatoryApi always includes credentials: "include"', async () => {
    const testObservatoryApi = new ObservatoryApiService({
      apiBaseUrl: 'http://127.0.0.1:8000',
      useMock: false,
    });
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({}),
    } as Response);

    await testObservatoryApi.getDashboard();
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/observatory/dashboard'),
      expect.objectContaining({ credentials: 'include' })
    );

    await testObservatoryApi.getEntries({ limit: 10 });
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/observatory/entries'),
      expect.objectContaining({ credentials: 'include' })
    );
  });

  it('supports empty base URL for same-origin relative endpoints in production', async () => {
    const { AuthApiService } = await import('../services/authApi');
    const prodAuth = new AuthApiService('');
    expect(prodAuth.getBaseUrl()).toBe('');

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ user: { email: 'test@example.com' } }),
    } as Response);

    await prodAuth.login({ email: 'test@example.com', password: 'secretpassword' });
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/auth/login',
      expect.objectContaining({ credentials: 'include' })
    );
  });
});
