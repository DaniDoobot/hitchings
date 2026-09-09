import React, { useEffect, useState, useMemo } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import {
  Search,
  Filter,
  X,
  Calendar,
  ExternalLink,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  SlidersHorizontal,
} from 'lucide-react';
import { observatoryApi } from '../services/observatoryApi';
import {
  ObservatoryEntryListItem,
  ObservatorySourceDetail,
  ObservatoryTopicNode,
  RelevanceStatus,
} from '../types/observatory';
import { RelevanceBadge } from '../components/common/RelevanceBadge';
import { TopicBadge } from '../components/common/TopicBadge';
import { CardSkeleton } from '../components/common/LoadingSkeleton';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';

export const ObservatoryPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  // Data states
  const [entries, setEntries] = useState<ObservatoryEntryListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [sources, setSources] = useState<ObservatorySourceDetail[]>([]);
  const [topics, setTopics] = useState<ObservatoryTopicNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Mobile filters drawer
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);

  // Active filters derived from URL
  const q = searchParams.get('q') || '';
  const relevanceStatus = (searchParams.get('relevance_status') as RelevanceStatus) || '';
  const minScore = searchParams.get('min_relevance_score') || '';
  const sourceId = searchParams.get('source_id') || '';
  const topicCode = searchParams.get('topic_code') || '';
  const dateFrom = searchParams.get('date_from') || '';
  const dateTo = searchParams.get('date_to') || '';
  const sortBy = searchParams.get('sort_by') || 'published_at';
  const sortOrder = searchParams.get('sort_order') || 'desc';
  const limit = parseInt(searchParams.get('limit') || '20', 10);
  const offset = parseInt(searchParams.get('offset') || '0', 10);

  // Local search text for debouncing
  const [searchInput, setSearchInput] = useState(q);

  // Load catalogs (sources, topics) once
  useEffect(() => {
    async function loadCatalogs() {
      try {
        const [srcs, topcs] = await Promise.all([
          observatoryApi.getSources(),
          observatoryApi.getTopics(),
        ]);
        setSources(srcs);
        setTopics(topcs);
      } catch (err) {
        console.error('Error cargando catálogos de filtro:', err);
      }
    }
    loadCatalogs();
  }, []);

  // Sync search input if URL changes externally
  useEffect(() => {
    setSearchInput(q);
  }, [q]);

  // Fetch entries when URL params change
  useEffect(() => {
    let isCancelled = false;
    async function fetchEntries() {
      setLoading(true);
      setError(null);
      try {
        const res = await observatoryApi.getEntries({
          limit,
          offset,
          sort_by: sortBy as 'published_at' | 'relevance_score',
          sort_order: sortOrder as 'asc' | 'desc',
          q: q || undefined,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          source_id: sourceId || undefined,
          relevance_status: (relevanceStatus as RelevanceStatus) || undefined,
          min_relevance_score: minScore ? parseInt(minScore, 10) : undefined,
          topic_code: topicCode || undefined,
        });

        if (!isCancelled) {
          setEntries(res.items);
          setTotal(res.total);
        }
      } catch (err: unknown) {
        if (!isCancelled) {
          setError(err instanceof Error ? err.message : 'Error al cargar las publicaciones');
        }
      } finally {
        if (!isCancelled) {
          setLoading(false);
        }
      }
    }

    fetchEntries();
    return () => {
      isCancelled = true;
    };
  }, [
    q,
    relevanceStatus,
    minScore,
    sourceId,
    topicCode,
    dateFrom,
    dateTo,
    sortBy,
    sortOrder,
    limit,
    offset,
  ]);

  // Helpers to update searchParams
  const updateFilter = (updates: Record<string, string | null>) => {
    const newParams = new URLSearchParams(searchParams);
    // Reset offset whenever filter criteria change (unless updating offset itself)
    if (!('offset' in updates)) {
      newParams.delete('offset');
    }

    Object.entries(updates).forEach(([key, val]) => {
      if (val === null || val === '') {
        newParams.delete(key);
      } else {
        newParams.set(key, val);
      }
    });

    setSearchParams(newParams);
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    updateFilter({ q: searchInput.trim() });
  };

  const clearAllFilters = () => {
    setSearchInput('');
    setSearchParams(new URLSearchParams());
  };

  // Flatten topics for select dropdown with indent
  const flatTopics = useMemo(() => {
    const res: { code: string; name: string; isChild: boolean }[] = [];
    topics.forEach((parent) => {
      res.push({ code: parent.code, name: parent.name, isChild: false });
      parent.children.forEach((child) => {
        res.push({ code: child.code, name: child.name, isChild: true });
      });
    });
    return res;
  }, [topics]);

  // Active filters count
  const activeFiltersCount = useMemo(() => {
    let count = 0;
    if (q) count++;
    if (relevanceStatus) count++;
    if (minScore) count++;
    if (sourceId) count++;
    if (topicCode) count++;
    if (dateFrom) count++;
    if (dateTo) count++;
    return count;
  }, [q, relevanceStatus, minScore, sourceId, topicCode, dateFrom, dateTo]);

  // Pagination calculation
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit) || 1;

  const currentSortValue = `${sortBy}_${sortOrder}`;
  const handleSortChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    if (val === 'published_at_desc') updateFilter({ sort_by: 'published_at', sort_order: 'desc' });
    else if (val === 'published_at_asc') updateFilter({ sort_by: 'published_at', sort_order: 'asc' });
    else if (val === 'relevance_score_desc') updateFilter({ sort_by: 'relevance_score', sort_order: 'desc' });
    else if (val === 'relevance_score_asc') updateFilter({ sort_by: 'relevance_score', sort_order: 'asc' });
  };

  return (
    <div className="space-y-6">
      {/* Page Heading & Search Bar */}
      <div className="bg-white rounded-lg border border-slate-200 p-6 shadow-sm">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
          <div>
            <h1 className="text-2xl font-bold font-serif text-slate-900">
              Observatorio de Publicaciones
            </h1>
            <p className="text-xs text-slate-500 mt-1">
              Fondo documental y jurisprudencial con análisis jurídico estructurado y evidencias directas.
            </p>
          </div>

          <button
            type="button"
            onClick={() => setMobileFilterOpen(true)}
            className="md:hidden inline-flex items-center gap-2 px-4 py-2 border border-slate-300 rounded-md text-sm font-medium text-slate-700 bg-white shadow-sm hover:bg-slate-50"
          >
            <SlidersHorizontal className="w-4 h-4" />
            <span>Filtros {activeFiltersCount > 0 && `(${activeFiltersCount})`}</span>
          </button>
        </div>

        {/* Global Search Input */}
        <form onSubmit={handleSearchSubmit} className="relative">
          <Search className="w-5 h-5 text-slate-400 absolute left-3.5 top-3" />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Buscar por término, tribunal, asunto, empresa o artículo legal (ej: 102 TFUE, Meta, distribución)..."
            className="w-full pl-11 pr-24 py-2.5 rounded-lg border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-navy-800 focus:border-navy-800 shadow-sm"
          />
          {searchInput && (
            <button
              type="button"
              onClick={() => {
                setSearchInput('');
                updateFilter({ q: null });
              }}
              className="absolute right-14 top-3 text-slate-400 hover:text-slate-600 p-0.5"
            >
              <X className="w-4 h-4" />
            </button>
          )}
          <button
            type="submit"
            className="absolute right-2 top-2 px-3 py-1.5 bg-navy-900 text-white rounded text-xs font-semibold hover:bg-navy-800 transition-colors shadow-sm"
          >
            Buscar
          </button>
        </form>

        {/* Active Filters Bar */}
        {activeFiltersCount > 0 && (
          <div className="flex flex-wrap items-center gap-2 mt-4 pt-4 border-t border-slate-100">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 mr-1">
              Filtros activos:
            </span>

            {q && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-100 text-xs font-medium text-slate-800 border border-slate-200">
                Texto: <strong className="text-slate-900">"{q}"</strong>
                <X className="w-3 h-3 cursor-pointer hover:text-red-600" onClick={() => updateFilter({ q: null })} />
              </span>
            )}

            {relevanceStatus && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-100 text-xs font-medium text-slate-800 border border-slate-200">
                Estado: {relevanceStatus === 'relevant' ? 'Relevante' : relevanceStatus === 'uncertain' ? 'En revisión' : 'No relevante'}
                <X className="w-3 h-3 cursor-pointer hover:text-red-600" onClick={() => updateFilter({ relevance_status: null })} />
              </span>
            )}

            {minScore && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-100 text-xs font-medium text-slate-800 border border-slate-200">
                Puntuación ≥ {minScore}
                <X className="w-3 h-3 cursor-pointer hover:text-red-600" onClick={() => updateFilter({ min_relevance_score: null })} />
              </span>
            )}

            {sourceId && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-100 text-xs font-medium text-slate-800 border border-slate-200">
                Fuente: {sources.find((s) => s.id === sourceId)?.name || 'Seleccionada'}
                <X className="w-3 h-3 cursor-pointer hover:text-red-600" onClick={() => updateFilter({ source_id: null })} />
              </span>
            )}

            {topicCode && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-100 text-xs font-medium text-slate-800 border border-slate-200">
                Tema: {flatTopics.find((t) => t.code === topicCode)?.name || topicCode}
                <X className="w-3 h-3 cursor-pointer hover:text-red-600" onClick={() => updateFilter({ topic_code: null })} />
              </span>
            )}

            {(dateFrom || dateTo) && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-100 text-xs font-medium text-slate-800 border border-slate-200">
                Fecha: {dateFrom || '...'} a {dateTo || '...'}
                <X className="w-3 h-3 cursor-pointer hover:text-red-600" onClick={() => updateFilter({ date_from: null, date_to: null })} />
              </span>
            )}

            <button
              onClick={clearAllFilters}
              className="text-xs text-navy-800 hover:text-navy-950 font-semibold underline ml-2"
            >
              Restablecer todos
            </button>
          </div>
        )}
      </div>

      {/* Main 2-Column Layout */}
      <div className="grid grid-cols-1 md:grid-cols-12 gap-8 items-start">
        {/* Left Filters Sidebar (Desktop) */}
        <aside className="hidden md:block md:col-span-4 lg:col-span-3 bg-white rounded-lg border border-slate-200 p-5 shadow-sm space-y-6 sticky top-24 max-h-[calc(100vh-7rem)] overflow-y-auto">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-800 flex items-center gap-2">
              <Filter className="w-3.5 h-3.5 text-navy-700" />
              Filtros Especializados
            </h3>
            {activeFiltersCount > 0 && (
              <button
                onClick={clearAllFilters}
                className="text-[11px] text-slate-500 hover:text-slate-800"
              >
                Limpiar
              </button>
            )}
          </div>

          {/* Relevance Status */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-2">
              Calificación de Relevancia
            </label>
            <div className="space-y-1.5">
              {[
                { id: '', label: 'Todas las calificaciones' },
                { id: 'relevant', label: 'Relevante' },
                { id: 'uncertain', label: 'En revisión' },
                { id: 'not_relevant', label: 'No relevante' },
              ].map((item) => (
                <label
                  key={item.id}
                  className="flex items-center gap-2 text-xs text-slate-700 cursor-pointer hover:text-slate-900"
                >
                  <input
                    type="radio"
                    name="relevance_status_desktop"
                    checked={relevanceStatus === item.id}
                    onChange={() => updateFilter({ relevance_status: item.id || null })}
                    className="text-navy-900 focus:ring-navy-800"
                  />
                  <span>{item.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Min Score */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-2">
              Puntuación mínima (0 - 100)
            </label>
            <select
              value={minScore}
              onChange={(e) => updateFilter({ min_relevance_score: e.target.value || null })}
              className="w-full text-xs rounded border-slate-300 py-2 px-2.5 bg-slate-50 focus:bg-white focus:ring-navy-800"
            >
              <option value="">Cualquier puntuación</option>
              <option value="90">≥ 90 puntos (Máxima prioridad)</option>
              <option value="80">≥ 80 puntos (Alta relevancia)</option>
              <option value="70">≥ 70 puntos (Significativa)</option>
              <option value="50">≥ 50 puntos (Media)</option>
            </select>
          </div>

          {/* Source Selector */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-2">
              Organismo / Fuente
            </label>
            <select
              value={sourceId}
              onChange={(e) => updateFilter({ source_id: e.target.value || null })}
              className="w-full text-xs rounded border-slate-300 py-2 px-2.5 bg-slate-50 focus:bg-white focus:ring-navy-800"
            >
              <option value="">Todas las fuentes</option>
              {sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.entry_count})
                </option>
              ))}
            </select>
          </div>

          {/* Topic Selector */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-2">
              Área o Materia Jurídica
            </label>
            <select
              value={topicCode}
              onChange={(e) => updateFilter({ topic_code: e.target.value || null })}
              className="w-full text-xs rounded border-slate-300 py-2 px-2.5 bg-slate-50 focus:bg-white focus:ring-navy-800 font-sans"
            >
              <option value="">Todos los temas (expansión activa)</option>
              {flatTopics.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.isChild ? `  ↳ ${t.name}` : `• ${t.name}`}
                </option>
              ))}
            </select>
            <p className="text-[10px] text-slate-400 mt-1.5">
              Al seleccionar un tema padre se incluyen automáticamente sus subtemas subordinados.
            </p>
          </div>

          {/* Date Range */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-2">
              Rango de Publicación
            </label>
            <div className="space-y-2">
              <div>
                <span className="text-[11px] text-slate-500 block mb-0.5">Desde</span>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => updateFilter({ date_from: e.target.value || null })}
                  className="w-full text-xs rounded border-slate-300 py-1.5 px-2 bg-slate-50 focus:bg-white focus:ring-navy-800"
                />
              </div>
              <div>
                <span className="text-[11px] text-slate-500 block mb-0.5">Hasta</span>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => updateFilter({ date_to: e.target.value || null })}
                  className="w-full text-xs rounded border-slate-300 py-1.5 px-2 bg-slate-50 focus:bg-white focus:ring-navy-800"
                />
              </div>
            </div>
          </div>
        </aside>

        {/* Right Content Area (List, Pagination, Sorting) */}
        <section className="md:col-span-8 lg:col-span-9 space-y-4">
          {/* Sorting and Count Bar */}
          <div className="bg-white rounded-lg border border-slate-200 px-4 py-3 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-slate-600">
            <div>
              Mostrando <strong className="text-slate-900">{total === 0 ? 0 : offset + 1}</strong> -{' '}
              <strong className="text-slate-900">{Math.min(offset + limit, total)}</strong> de{' '}
              <strong className="text-slate-900">{total}</strong> publicaciones
            </div>

            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <ArrowUpDown className="w-3.5 h-3.5 text-slate-400" />
                <span>Ordenar:</span>
              </div>
              <select
                value={currentSortValue}
                onChange={handleSortChange}
                className="rounded border-slate-300 py-1 px-2.5 text-xs bg-slate-50 focus:bg-white focus:ring-navy-800 font-medium text-slate-800"
              >
                <option value="published_at_desc">Fecha (más reciente primero)</option>
                <option value="published_at_asc">Fecha (más antigua primero)</option>
                <option value="relevance_score_desc">Relevancia (mayor puntuación)</option>
                <option value="relevance_score_asc">Relevancia (menor puntuación)</option>
              </select>
            </div>
          </div>

          {/* List Content */}
          {error ? (
            <ErrorState message={error} onRetry={() => updateFilter({})} />
          ) : loading ? (
            <div className="space-y-4">
              <CardSkeleton />
              <CardSkeleton />
              <CardSkeleton />
            </div>
          ) : entries.length === 0 ? (
            <EmptyState onClearFilters={clearAllFilters} />
          ) : (
            <div className="space-y-4">
              {entries.map((entry) => (
                <article
                  key={entry.entry_id}
                  className="bg-white rounded-lg border border-slate-200 p-5 shadow-sm hover:border-navy-300 hover:shadow-md transition-all group"
                >
                  {/* Card Header: Source & Relevance Badge */}
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-semibold text-navy-800 bg-navy-50 px-2.5 py-0.5 rounded border border-navy-100">
                        {entry.source.name}
                      </span>
                      {entry.content_type && (
                        <span className="text-[11px] uppercase tracking-wider text-slate-500 bg-slate-100 px-2 py-0.5 rounded font-mono">
                          {entry.content_type}
                        </span>
                      )}
                      <span className="text-xs text-slate-400 flex items-center gap-1">
                        <Calendar className="w-3 h-3" />
                        {entry.published_at ? (
                          new Date(entry.published_at).toLocaleDateString('es-ES', {
                            day: 'numeric',
                            month: 'long',
                            year: 'numeric',
                          })
                        ) : (
                          'Fecha no disponible'
                        )}
                      </span>
                    </div>

                    <RelevanceBadge
                      status={entry.relevance.status}
                      score={entry.relevance.score}
                      size="sm"
                    />
                  </div>

                  {/* Title */}
                  <Link
                    to={`/observatorio/${entry.entry_id}`}
                    className="text-base sm:text-lg font-bold font-serif text-slate-900 group-hover:text-navy-700 transition-colors block mb-2 leading-snug"
                  >
                    {entry.title}
                  </Link>

                  {/* Executive Summary */}
                  <p className="text-xs sm:text-sm text-slate-600 leading-relaxed mb-3.5">
                    {entry.summary}
                  </p>

                  {/* Key points preview (up to 2) */}
                  {entry.key_points && entry.key_points.length > 0 && (
                    <div className="mb-4 bg-slate-50 rounded-md p-3 border border-slate-100 space-y-1.5">
                      <div className="text-[11px] font-bold text-slate-700 uppercase tracking-wide flex items-center gap-1.5">
                        <Sparkles className="w-3 h-3 text-legal-gold" />
                        Puntos Clave Jurídicos
                      </div>
                      <ul className="space-y-1">
                        {entry.key_points.slice(0, 2).map((kp, idx) => (
                          <li key={idx} className="text-xs text-slate-600 flex items-start gap-2">
                            <span className="text-slate-400 font-mono">•</span>
                            <span>{kp}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Card Footer: Canonical Topics & Actions */}
                  <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-100">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {(() => {
                        const allTopics = [
                          ...(entry.canonical_primary_topic
                            ? [{ ...entry.canonical_primary_topic, isPrimary: true }]
                            : []),
                          ...entry.canonical_topics
                            .filter((t) => t.code !== entry.canonical_primary_topic?.code)
                            .map((t) => ({ ...t, isPrimary: false })),
                        ];
                        const visible = allTopics.slice(0, 3);
                        const extraCount = allTopics.length - 3;
                        return (
                          <>
                            {visible.map((t) => (
                              <TopicBadge
                                key={t.code}
                                name={t.name}
                                isPrimary={t.isPrimary}
                                onClick={() => updateFilter({ topic_code: t.code })}
                              />
                            ))}
                            {extraCount > 0 && (
                              <span
                                className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-slate-100 text-slate-600 border border-slate-200"
                                title={`${extraCount} materias adicionales`}
                              >
                                +{extraCount} más
                              </span>
                            )}
                          </>
                        );
                      })()}
                    </div>

                    <div className="flex items-center gap-3 ml-auto text-xs">
                      {entry.url && (
                        <a
                          href={entry.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-slate-500 hover:text-slate-800 inline-flex items-center gap-1.5 font-medium transition-colors"
                          aria-label={`Acceder a la publicación original en nueva pestaña: ${entry.title}`}
                        >
                          <span>Acceder a la publicación original</span>
                          <ExternalLink className="w-3.5 h-3.5 text-slate-400" aria-hidden="true" />
                        </a>
                      )}
                      <Link
                        to={`/observatorio/${entry.entry_id}`}
                        className="px-3 py-1.5 bg-navy-900 hover:bg-navy-800 text-white rounded font-medium shadow-sm transition-colors"
                      >
                        Ver análisis
                      </Link>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}

          {/* Pagination Controls */}
          {totalPages > 1 && (
            <div className="bg-white rounded-lg border border-slate-200 px-4 py-3 shadow-sm flex items-center justify-between">
              <button
                type="button"
                disabled={currentPage <= 1}
                onClick={() => updateFilter({ offset: String(Math.max(0, offset - limit)) })}
                className="inline-flex items-center gap-1 px-3 py-1.5 border border-slate-300 rounded text-xs font-semibold text-slate-700 bg-white hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed shadow-sm"
              >
                <ChevronLeft className="w-4 h-4" />
                <span>Anterior</span>
              </button>

              <span className="text-xs text-slate-600 font-medium">
                Página <strong className="text-slate-900">{currentPage}</strong> de{' '}
                <strong className="text-slate-900">{totalPages}</strong>
              </span>

              <button
                type="button"
                disabled={currentPage >= totalPages}
                onClick={() => updateFilter({ offset: String(offset + limit) })}
                className="inline-flex items-center gap-1 px-3 py-1.5 border border-slate-300 rounded text-xs font-semibold text-slate-700 bg-white hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed shadow-sm"
              >
                <span>Siguiente</span>
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
        </section>
      </div>

      {/* Mobile Filters Modal */}
      {mobileFilterOpen && (
        <div className="fixed inset-0 z-50 flex md:hidden">
          <div
            className="fixed inset-0 bg-navy-950/60 backdrop-blur-sm"
            onClick={() => setMobileFilterOpen(false)}
          />
          <div className="relative ml-auto w-full max-w-xs bg-white h-full p-6 shadow-xl flex flex-col justify-between overflow-y-auto">
            <div className="space-y-6">
              <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                <h3 className="font-bold text-slate-900 text-sm">Filtros Especializados</h3>
                <button
                  type="button"
                  onClick={() => setMobileFilterOpen(false)}
                  className="p-1 rounded text-slate-400 hover:text-slate-600"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Status */}
              <div>
                <label className="block text-xs font-semibold uppercase text-slate-700 mb-2">
                  Calificación
                </label>
                <select
                  value={relevanceStatus}
                  onChange={(e) => updateFilter({ relevance_status: e.target.value || null })}
                  className="w-full text-xs rounded border-slate-300 py-2 px-2"
                >
                  <option value="">Todas las calificaciones</option>
                  <option value="relevant">Relevante</option>
                  <option value="uncertain">En revisión</option>
                  <option value="not_relevant">No relevante</option>
                </select>
              </div>

              {/* Source */}
              <div>
                <label className="block text-xs font-semibold uppercase text-slate-700 mb-2">
                  Organismo / Fuente
                </label>
                <select
                  value={sourceId}
                  onChange={(e) => updateFilter({ source_id: e.target.value || null })}
                  className="w-full text-xs rounded border-slate-300 py-2 px-2"
                >
                  <option value="">Todas las fuentes</option>
                  {sources.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Topic */}
              <div>
                <label className="block text-xs font-semibold uppercase text-slate-700 mb-2">
                  Tema Jurídico
                </label>
                <select
                  value={topicCode}
                  onChange={(e) => updateFilter({ topic_code: e.target.value || null })}
                  className="w-full text-xs rounded border-slate-300 py-2 px-2"
                >
                  <option value="">Todos los temas</option>
                  {flatTopics.map((t) => (
                    <option key={t.code} value={t.code}>
                      {t.isChild ? `  ${t.name}` : t.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="pt-6 border-t border-slate-100 flex gap-2">
              <button
                type="button"
                onClick={clearAllFilters}
                className="w-1/2 py-2 border border-slate-300 rounded text-xs font-semibold text-slate-700"
              >
                Limpiar
              </button>
              <button
                type="button"
                onClick={() => setMobileFilterOpen(false)}
                className="w-1/2 py-2 bg-navy-900 text-white rounded text-xs font-semibold"
              >
                Aplicar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
