import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ProtectedRoute } from './components/auth/ProtectedRoute';
import { AdminRoute } from './components/auth/AdminRoute';
import { AppLayout } from './components/layout/AppLayout';
import { LoginPage } from './pages/LoginPage';
import { DashboardPage } from './pages/DashboardPage';
import { ObservatoryPage } from './pages/ObservatoryPage';
import { EntryDetailPage } from './pages/EntryDetailPage';
import { DocumentsPage } from './pages/DocumentsPage';
import { DocumentDetailPage } from './pages/DocumentDetailPage';
import { AdminUsersPage } from './pages/AdminUsersPage';

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public Authentication Route */}
          <Route path="/login" element={<LoginPage />} />

          {/* Protected Client Portal Routes */}
          <Route element={<ProtectedRoute />}>
            <Route
              path="/"
              element={
                <AppLayout>
                  <DashboardPage />
                </AppLayout>
              }
            />
            <Route
              path="/observatorio"
              element={
                <AppLayout>
                  <ObservatoryPage />
                </AppLayout>
              }
            />
            <Route
              path="/observatorio/:entryId"
              element={
                <AppLayout>
                  <EntryDetailPage />
                </AppLayout>
              }
            />
            <Route
              path="/documentos"
              element={
                <AppLayout>
                  <DocumentsPage />
                </AppLayout>
              }
            />
            <Route
              path="/documentos/:documentId"
              element={
                <AppLayout>
                  <DocumentDetailPage />
                </AppLayout>
              }
            />

            {/* Dedicated Administrative Routes */}
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
          </Route>

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
};

export default App;

