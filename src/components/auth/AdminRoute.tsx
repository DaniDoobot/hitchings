import React from 'react';
import { Navigate, useLocation, Link, Outlet } from 'react-router-dom';
import { ShieldAlert, ArrowLeft } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

interface AdminRouteProps {
  children?: React.ReactNode;
}

export const AdminRoute: React.FC<AdminRouteProps> = ({ children }) => {
  const { user, status } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
        <div className="flex flex-col items-center gap-3 text-slate-500">
          <div className="w-7 h-7 border-2 border-navy-900 border-t-transparent rounded-full animate-spin" />
          <span className="text-xs font-medium">Verificando permisos administrativos...</span>
        </div>
      </div>
    );
  }

  if (status === 'unauthenticated') {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (user?.role !== 'admin') {
    return (
      <div className="min-h-[70vh] flex items-center justify-center p-4">
        <div className="max-w-md w-full bg-white rounded-xl shadow-sm border border-slate-200 p-8 text-center">
          <div className="w-12 h-12 rounded-full bg-red-50 border border-red-200 flex items-center justify-center text-red-600 mx-auto mb-4">
            <ShieldAlert className="w-6 h-6" />
          </div>
          <h2 className="text-xl font-bold font-serif text-slate-900 mb-2">
            403 — Acceso Restringido
          </h2>
          <p className="text-xs text-slate-600 leading-relaxed mb-6">
            Esta sección está reservada exclusivamente para usuarios con privilegios de administrador. Tu cuenta actual (<span className="font-semibold text-slate-800">{user?.email}</span>) tiene rol de usuario estándar.
          </p>
          <Link
            to="/"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-navy-900 hover:bg-navy-800 text-white text-xs font-semibold transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Volver al Cuadro de Mando</span>
          </Link>
        </div>
      </div>
    );
  }

  return children ? <>{children}</> : <Outlet />;
};
