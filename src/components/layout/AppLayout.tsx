import React, { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Compass,
  Menu,
  X,
  Scale,
  Database,
  ExternalLink,
} from 'lucide-react';
import { observatoryApi } from '../../services/observatoryApi';

interface AppLayoutProps {
  children: React.ReactNode;
}

export const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const location = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const isMock = observatoryApi.isUsingMock();

  const navItems = [
    { name: 'Cuadro de Mando', path: '/', icon: LayoutDashboard },
    { name: 'Observatorio', path: '/observatorio', icon: Compass },
  ];

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/';
    return location.pathname.startsWith(path);
  };

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      {/* Top Header */}
      <header className="bg-navy-950 text-white border-b border-navy-900 sticky top-0 z-30 shadow-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            {/* Brand Logo & Name */}
            <div className="flex items-center gap-3">
              <Link to="/" className="flex items-center gap-3 group">
                <div className="w-10 h-10 rounded bg-navy-800 border border-navy-700 flex items-center justify-center text-legal-gold group-hover:border-legal-gold transition-colors">
                  <Scale className="w-5 h-5" />
                </div>
                <div>
                  <span className="text-xl font-bold tracking-tight font-serif text-white flex items-center gap-2">
                    HITCHINGS
                    <span className="text-[10px] font-sans font-semibold tracking-wider uppercase px-1.5 py-0.5 rounded bg-legal-gold/20 text-legal-gold border border-legal-gold/40">
                      LEGAL AI
                    </span>
                  </span>
                  <span className="text-xs text-navy-300 block font-sans tracking-wide">
                    Observatorio de Derecho de la Competencia
                  </span>
                </div>
              </Link>
            </div>

            {/* Desktop Navigation Links */}
            <nav className="hidden md:flex items-center gap-1">
              {navItems.map((item) => {
                const Icon = item.icon;
                const active = isActive(item.path);
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={`flex items-center gap-2 px-3.5 py-2 rounded-md text-sm font-medium transition-all ${
                      active
                        ? 'bg-navy-800 text-white shadow-inner font-semibold'
                        : 'text-navy-200 hover:text-white hover:bg-navy-900'
                    }`}
                  >
                    <Icon className={`w-4 h-4 ${active ? 'text-legal-gold' : 'text-navy-400'}`} />
                    <span>{item.name}</span>
                  </Link>
                );
              })}
            </nav>

            {/* Right Status Badge & Actions */}
            <div className="hidden sm:flex items-center gap-4">
              <div
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono border ${
                  isMock
                    ? 'bg-navy-900/80 text-amber-300 border-amber-500/30'
                    : 'bg-emerald-950/80 text-emerald-300 border-emerald-500/40'
                }`}
                title={
                  isMock
                    ? 'Ejecutándose en modo autónomo con datos mock locales'
                    : 'Conectado a la API REST del backend'
                }
              >
                <Database className="w-3 h-3" />
                <span>{isMock ? 'Mock Dataset' : 'API Conectada'}</span>
              </div>
            </div>

            {/* Mobile Menu Button */}
            <div className="flex md:hidden">
              <button
                type="button"
                onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                className="p-2 rounded-md text-navy-300 hover:text-white hover:bg-navy-900 focus:outline-none"
                aria-label="Abrir menú"
              >
                {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
              </button>
            </div>
          </div>
        </div>

        {/* Mobile Navigation Drawer */}
        {mobileMenuOpen && (
          <div className="md:hidden border-t border-navy-800 bg-navy-950 px-4 pt-2 pb-4 space-y-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = isActive(item.path);
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  onClick={() => setMobileMenuOpen(false)}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-md text-base font-medium ${
                    active
                      ? 'bg-navy-800 text-white font-semibold'
                      : 'text-navy-200 hover:text-white hover:bg-navy-900'
                  }`}
                >
                  <Icon className={`w-5 h-5 ${active ? 'text-legal-gold' : 'text-navy-400'}`} />
                  <span>{item.name}</span>
                </Link>
              );
            })}
            <div className="pt-3 border-t border-navy-800 mt-2">
              <div className="flex items-center gap-2 px-3 py-2 text-xs text-navy-300">
                <Database className="w-4 h-4" />
                <span>Modo: {isMock ? 'Mock Dataset' : 'API Backend Conectada'}</span>
              </div>
            </div>
          </div>
        )}
      </header>

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {children}
      </main>

      {/* Professional Footer */}
      <footer className="bg-white border-t border-slate-200 text-slate-500 text-xs py-6 mt-12">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-slate-700">HITCHINGS</span>
            <span>—</span>
            <span>Plataforma de Inteligencia Jurídica en Derecho de la Competencia</span>
          </div>
          <div className="flex items-center gap-6">
            <span className="text-slate-400">
              Datos consolidados de resoluciones y sentencias judiciales (CAT, TJUE, CNMC, DG Comp)
            </span>
            <a
              href="https://curia.europa.eu"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-slate-800 inline-flex items-center gap-1 transition-colors"
            >
              Curia <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
};
