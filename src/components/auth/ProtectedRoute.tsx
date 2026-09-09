import React from 'react';
import { Navigate, useLocation, Outlet } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';

interface ProtectedRouteProps {
  children?: React.ReactNode;
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
  const { status } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
        <div className="flex flex-col items-center gap-3 text-slate-500">
          <div className="w-7 h-7 border-2 border-navy-900 border-t-transparent rounded-full animate-spin" />
          <span className="text-xs font-medium">Verificando sesión autorizada...</span>
        </div>
      </div>
    );
  }

  if (status === 'unauthenticated') {
    // Preserve full intended URL (pathname + search params) to return seamlessly after login
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return children ? <>{children}</> : <Outlet />;
};
