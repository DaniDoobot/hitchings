import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  FileText,
  TrendingUp,
  Award,
  Clock,
  ArrowRight,
  Search,
  Layers,
  Building2,
  Calendar,
} from 'lucide-react';
import { observatoryApi } from '../services/observatoryApi';
import { ObservatoryDashboard } from '../types/observatory';
import { RelevanceBadge } from '../components/common/RelevanceBadge';
import { TopicBadge } from '../components/common/TopicBadge';
import { KPISkeleton, CardSkeleton } from '../components/common/LoadingSkeleton';
import { ErrorState } from '../components/common/ErrorState';

export const DashboardPage: React.FC = () => {
  const navigate = useNavigate();
  const [dashboard, setDashboard] = useState<ObservatoryDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await observatoryApi.getDashboard();
      setDashboard(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error desconocido al cargar el cuadro de mando');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      navigate(`/observatorio?q=${encodeURIComponent(searchQuery.trim())}`);
    } else {
      navigate('/observatorio');
    }
  };

  if (error) {
    return <ErrorState message={error} onRetry={loadData} />;
  }

  return (
    <div className="space-y-8">
      {/* Hero / Introduction Header */}
      <div className="bg-gradient-to-r from-navy-950 via-navy-900 to-navy-800 rounded-xl p-6 sm:p-8 text-white shadow-lg border border-navy-700/50">
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-legal-gold/20 text-legal-gold text-xs font-semibold uppercase tracking-wider mb-4 border border-legal-gold/30">
            <TrendingUp className="w-3.5 h-3.5" />
            Observatorio Activo
          </div>
          <h1 className="text-2xl sm:text-3xl font-bold font-serif tracking-tight mb-2">
            Monitorización Estratégica en Derecho de la Competencia
          </h1>
          <p className="text-sm sm:text-base text-navy-200 leading-relaxed mb-6">
            Análisis jurídico y clasificación de resoluciones oficiales de tribunales y agencias reguladoras
            (CAT, TJUE, CNMC, DG Comp). Detección temprana de precedentes, acciones colectivas y cárteles.
          </p>

          {/* Quick Search Form */}
          <form onSubmit={handleSearchSubmit} className="flex flex-col sm:flex-row gap-2 max-w-xl">
            <div className="relative flex-1">
              <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-3.5" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Buscar por asunto, empresa, artículo (ej: 102 TFUE, Meta, cártel)..."
                className="w-full pl-10 pr-4 py-2.5 rounded-lg bg-white/95 text-slate-900 placeholder-slate-400 text-sm focus:outline-none focus:ring-2 focus:ring-legal-gold focus:bg-white shadow-sm"
              />
            </div>
            <button
              type="submit"
              className="px-5 py-2.5 bg-legal-gold hover:bg-legal-goldDark text-navy-950 font-semibold rounded-lg text-sm transition-colors shadow-sm flex items-center justify-center gap-2"
            >
              <span>Explorar</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </form>
        </div>
      </div>

      {/* KPI Metrics Section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-bold text-slate-900 flex items-center gap-2 uppercase tracking-wide text-xs">
            <FileText className="w-4 h-4 text-navy-700" />
            Estado del Fondo Documental
          </h2>
          <span className="text-xs text-slate-500">
            Total publicaciones: <strong className="text-slate-800">{dashboard?.total_publications || 0}</strong>
          </span>
        </div>

        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <KPISkeleton />
            <KPISkeleton />
            <KPISkeleton />
            <KPISkeleton />
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Total Relevantes */}
            <div
              onClick={() => navigate('/observatorio?relevance_status=relevant')}
              className="bg-white rounded-lg border border-slate-200 p-5 shadow-sm hover:border-emerald-500/50 hover:shadow-md transition-all cursor-pointer group"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-emerald-700">
                  Relevantes
                </span>
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold font-serif text-slate-900 group-hover:text-emerald-700 transition-colors">
                  {dashboard?.relevant_count || 0}
                </span>
                <span className="text-xs text-slate-500">
                  ({Math.round(((dashboard?.relevant_count || 0) / (dashboard?.total_publications || 1)) * 100)}% del total)
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Precedentes de impacto procesal o sustantivo directo.
              </p>
            </div>

            {/* En Revisión / Inciertas */}
            <div
              onClick={() => navigate('/observatorio?relevance_status=uncertain')}
              className="bg-white rounded-lg border border-slate-200 p-5 shadow-sm hover:border-amber-500/50 hover:shadow-md transition-all cursor-pointer group"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-amber-700">
                  En Revisión
                </span>
                <span className="w-2.5 h-2.5 rounded-full bg-amber-400"></span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold font-serif text-slate-900 group-hover:text-amber-700 transition-colors">
                  {dashboard?.uncertain_count || 0}
                </span>
                <span className="text-xs text-slate-500">
                  ({Math.round(((dashboard?.uncertain_count || 0) / (dashboard?.total_publications || 1)) * 100)}%)
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Avisos preliminares o impacto potencial indirecto.
              </p>
            </div>

            {/* No relevantes */}
            <div
              onClick={() => navigate('/observatorio?relevance_status=not_relevant')}
              className="bg-white rounded-lg border border-slate-200 p-5 shadow-sm hover:border-slate-400 transition-all cursor-pointer group"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-600">
                  Descartadas
                </span>
                <span className="w-2.5 h-2.5 rounded-full bg-slate-300"></span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold font-serif text-slate-900">
                  {dashboard?.not_relevant_count || 0}
                </span>
                <span className="text-xs text-slate-500">
                  ({Math.round(((dashboard?.not_relevant_count || 0) / (dashboard?.total_publications || 1)) * 100)}%)
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Asuntos de trámite ordinario o sin afección competitiva.
              </p>
            </div>

            {/* Actividad últimos 30 días */}
            <div className="bg-slate-900 text-white rounded-lg p-5 shadow-sm border border-slate-800">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-legal-gold">
                  Últimos 30 días
                </span>
                <Clock className="w-4 h-4 text-slate-400" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold font-serif text-white">
                  {dashboard?.publications_last_30_days || 0}
                </span>
                <span className="text-xs text-slate-300">publicaciones</span>
              </div>
              <div className="flex items-center gap-3 text-xs text-slate-300 mt-2 pt-2 border-t border-slate-800">
                <span>
                  <strong className="text-emerald-400 font-semibold">{dashboard?.relevant_last_30_days || 0}</strong> relevantes
                </span>
                <span>•</span>
                <span>
                  <strong>{dashboard?.publications_last_7_days || 0}</strong> en 7 días
                </span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Grid: Top Temas & Distribución de Fuentes */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Top Topics (7 cols) */}
        <div className="lg:col-span-7 bg-white rounded-lg border border-slate-200 p-6 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-bold text-slate-900 uppercase tracking-wide flex items-center gap-2">
              <Layers className="w-4 h-4 text-navy-700" />
              Áreas y Temas con Mayor Actividad
            </h3>
            <Link
              to="/observatorio"
              className="text-xs text-navy-700 hover:text-navy-900 font-semibold flex items-center gap-1"
            >
              Ver todos <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
          <p className="text-xs text-slate-500 mb-5">
            Clasificación canónica no redundante de los asuntos con análisis jurídico vigente.
          </p>

          <div className="space-y-3">
            {dashboard?.top_topics && dashboard.top_topics.length > 0 ? (
              dashboard.top_topics.map((topic) => {
                const maxCount = dashboard.top_topics[0]?.count || 1;
                const percentage = Math.round((topic.count / maxCount) * 100);
                return (
                  <div
                    key={topic.code}
                    onClick={() => navigate(`/observatorio?topic_code=${topic.code}`)}
                    className="p-3 rounded-lg border border-slate-100 hover:border-slate-300 hover:bg-slate-50/70 transition-all cursor-pointer"
                  >
                    <div className="flex items-center justify-between text-sm mb-1.5">
                      <span className="font-semibold text-slate-800">{topic.name}</span>
                      <span className="text-xs font-mono font-semibold bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                        {topic.count} {topic.count === 1 ? 'publicación' : 'publicaciones'}
                      </span>
                    </div>
                    {/* Progress Bar */}
                    <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                      <div
                        className="bg-navy-700 h-1.5 rounded-full transition-all duration-500"
                        style={{ width: `${percentage}%` }}
                      ></div>
                    </div>
                  </div>
                );
              })
            ) : (
              <p className="text-xs text-slate-400 py-4 text-center">No hay datos de temas disponibles.</p>
            )}
          </div>
        </div>

        {/* Top Sources (5 cols) */}
        <div className="lg:col-span-5 bg-white rounded-lg border border-slate-200 p-6 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-bold text-slate-900 uppercase tracking-wide flex items-center gap-2">
              <Building2 className="w-4 h-4 text-navy-700" />
              Fuentes Monitorizadas
            </h3>
            <span className="text-xs font-mono text-slate-500">
              {dashboard?.top_sources.length || 0} fuentes activas
            </span>
          </div>
          <p className="text-xs text-slate-500 mb-5">
            Volumen procesado e índice de relevancia detectado por organismo emisor.
          </p>

          <div className="space-y-3">
            {dashboard?.top_sources.map((src) => (
              <div
                key={src.source_id}
                onClick={() => navigate(`/observatorio?source_id=${src.source_id}`)}
                className="p-3 rounded-lg border border-slate-100 hover:border-slate-300 hover:bg-slate-50/70 transition-all cursor-pointer"
              >
                <div className="font-medium text-sm text-slate-900 mb-1 line-clamp-1">
                  {src.name}
                </div>
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span>{src.publication_count} resoluciones</span>
                  <span className="text-emerald-700 font-semibold">
                    {src.relevant_count} relevantes ({Math.round((src.relevant_count / (src.publication_count || 1)) * 100)}%)
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Latest Relevant Entries (5) */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-900 uppercase tracking-wide flex items-center gap-2">
              <Award className="w-4 h-4 text-legal-gold" />
              Últimas Publicaciones Relevantes
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Resoluciones más recientes de alta trascendencia analítica seleccionadas por el Observatorio.
            </p>
          </div>
          <Link
            to="/observatorio?relevance_status=relevant"
            className="inline-flex items-center gap-1.5 px-3.5 py-1.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 text-xs font-semibold rounded-md shadow-sm transition-colors"
          >
            <span>Ver todas las relevantes</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>

        {loading ? (
          <div className="space-y-3">
            <CardSkeleton />
            <CardSkeleton />
          </div>
        ) : (
          <div className="space-y-3">
            {dashboard?.latest_relevant_entries.map((entry) => (
              <div
                key={entry.entry_id}
                className="bg-white rounded-lg border border-slate-200 p-5 shadow-sm hover:border-navy-300 hover:shadow-md transition-all"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-semibold text-navy-800 bg-navy-50 px-2.5 py-0.5 rounded border border-navy-100">
                      {entry.source.name}
                    </span>
                    <span className="text-xs text-slate-400 flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      {new Date(entry.published_at).toLocaleDateString('es-ES', {
                        day: 'numeric',
                        month: 'short',
                        year: 'numeric',
                      })}
                    </span>
                  </div>
                  <RelevanceBadge status="relevant" score={entry.score} size="sm" />
                </div>

                <Link
                  to={`/observatorio/${entry.entry_id}`}
                  className="text-base font-bold text-slate-900 hover:text-navy-700 transition-colors font-serif block mb-2"
                >
                  {entry.title}
                </Link>

                <p className="text-xs text-slate-600 line-clamp-2 leading-relaxed mb-3">
                  {entry.summary}
                </p>

                <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-100">
                  <div className="flex flex-wrap gap-1.5">
                    {entry.canonical_topics.map((t) => (
                      <TopicBadge
                        key={t.code}
                        name={t.name}
                        onClick={() => navigate(`/observatorio?topic_code=${t.code}`)}
                      />
                    ))}
                  </div>

                  <Link
                    to={`/observatorio/${entry.entry_id}`}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-navy-800 hover:text-navy-950 transition-colors ml-auto"
                  >
                    <span>Ficha jurídica</span>
                    <ArrowRight className="w-3 h-3" />
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
