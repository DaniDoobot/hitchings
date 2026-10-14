import React, { useEffect, useState, useRef } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import {
  Search,
  X,
  Calendar,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Clock,
  Building2,
  User,
  FolderArchive,
  Plus,
  FileDown,
  AlertTriangle,
} from 'lucide-react';
import {
  documentsApi,
  triggerBrowserDownload,
  mapDossierExportErrorMessage,
} from '../services/documentsApi';
import { observatoryApi } from '../services/observatoryApi';
import { DocumentListItem } from '../types/document';
import { ObservatorySourceDetail, OriginCategory } from '../types/observatory';
import { RelevanceBadge } from '../components/common/RelevanceBadge';
import { TopicBadge } from '../components/common/TopicBadge';
import { CardSkeleton } from '../components/common/LoadingSkeleton';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';
import { AddDocumentModal } from '../components/documents/AddDocumentModal';
import { ExportDossierModal } from '../components/documents/ExportDossierModal';
import { formatDocumentTitle } from '../utils/documentFormatter';

const MAX_DOSSIER_DOCUMENTS = 30;

export const DocumentsPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [sources, setSources] = useState<ObservatorySourceDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Active filters derived from URL
  const q = searchParams.get('q') || '';
  const hasAnalysis = (searchParams.get('has_analysis') as 'all' | 'with_analysis' | 'without_analysis') || 'all';
  const sourceId = searchParams.get('source_id') || '';
  const originCategory = searchParams.get('origin_category') || '';
  const dateFrom = searchParams.get('date_from') || '';
  const dateTo = searchParams.get('date_to') || '';
  const sortBy = searchParams.get('sort_by') || 'published_at';
  const sortOrder = searchParams.get('sort_order') || 'desc';
  const limit = parseInt(searchParams.get('limit') || '20', 10);
  const offset = parseInt(searchParams.get('offset') || '0', 10);

  const [searchInput, setSearchInput] = useState(q);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  // Selection & Dossier Export state (Bloque 12 — Fase 4C)
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [isDossierModalOpen, setIsDossierModalOpen] = useState(false);
  const [isGeneratingDossier, setIsGeneratingDossier] = useState(false);
  const [dossierError, setDossierError] = useState<string | null>(null);
  const [dossierSuccess, setDossierSuccess] = useState<string | null>(null);
  const [dossierWarning, setDossierWarning] = useState<string | null>(null);
  const masterCheckboxRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    async function loadSources() {
      try {
        const srcs = await observatoryApi.getSources();
        setSources(srcs);
      } catch (err) {
        console.error('Error cargando fuentes:', err);
      }
    }
    loadSources();
  }, []);

  useEffect(() => {
    setSearchInput(q);
  }, [q]);

  useEffect(() => {
    let isCancelled = false;
    async function fetchDocs() {
      setLoading(true);
      setError(null);
      try {
        const res = await documentsApi.getDocuments({
          limit,
          offset,
          sort_by: sortBy as 'published_at' | 'captured_at' | 'title',
          sort_order: sortOrder as 'asc' | 'desc',
          q: q || undefined,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          source_id: sourceId || undefined,
          origin_category: originCategory && originCategory !== 'all' ? (originCategory as OriginCategory) : undefined,
          has_analysis: hasAnalysis !== 'all' ? hasAnalysis : undefined,
        });
        if (!isCancelled) {
          setDocuments(res.items);
          setTotal(res.total);
        }
      } catch (err: unknown) {
        if (!isCancelled) {
          setError(err instanceof Error ? err.message : 'Error al cargar fondo documental');
        }
      } finally {
        if (!isCancelled) {
          setLoading(false);
        }
      }
    }
    fetchDocs();
    return () => {
      isCancelled = true;
    };
  }, [q, hasAnalysis, sourceId, originCategory, dateFrom, dateTo, sortBy, sortOrder, limit, offset, refreshTrigger]);

  const updateParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === '' || value === 'all') {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    if (key !== 'offset') {
      next.set('offset', '0');
    }
    setSearchParams(next);
  };

  const clearAllFilters = () => {
    setSearchParams(new URLSearchParams());
    setSearchInput('');
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    updateParam('q', searchInput.trim() || null);
  };

  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit) || 1;

  // Multi-selection computed values
  const currentPageIds = documents.map((d) => d.id);
  const selectedInCurrentPage = currentPageIds.filter((id) => selectedDocIds.includes(id));
  const isAllInPageSelected =
    currentPageIds.length > 0 && selectedInCurrentPage.length === currentPageIds.length;
  const isSomeInPageSelected =
    selectedInCurrentPage.length > 0 && !isAllInPageSelected;

  useEffect(() => {
    if (masterCheckboxRef.current) {
      masterCheckboxRef.current.indeterminate = isSomeInPageSelected;
    }
  }, [isSomeInPageSelected]);

  const toggleSelectDocument = (id: string) => {
    setSelectedDocIds((prev) => {
      if (prev.includes(id)) {
        return prev.filter((item) => item !== id);
      }
      if (prev.length >= MAX_DOSSIER_DOCUMENTS) {
        setDossierWarning('Límite de 30 documentos alcanzado. No es posible seleccionar más.');
        setTimeout(() => setDossierWarning(null), 4000);
        return prev;
      }
      return [...prev, id];
    });
  };

  const toggleSelectAllInPage = () => {
    if (isAllInPageSelected) {
      // Deselect all visible on this page
      setSelectedDocIds((prev) => prev.filter((id) => !currentPageIds.includes(id)));
    } else {
      // Select up to 30
      const unselectedInPage = currentPageIds.filter((id) => !selectedDocIds.includes(id));
      const availableSlots = MAX_DOSSIER_DOCUMENTS - selectedDocIds.length;
      if (availableSlots <= 0) {
        setDossierWarning('Límite de 30 documentos alcanzado. No es posible seleccionar más.');
        setTimeout(() => setDossierWarning(null), 4000);
        return;
      }
      const toAdd = unselectedInPage.slice(0, availableSlots);
      if (unselectedInPage.length > availableSlots) {
        setDossierWarning('Se han seleccionado documentos hasta completar el límite máximo de 30.');
        setTimeout(() => setDossierWarning(null), 5000);
      }
      setSelectedDocIds((prev) => [...prev, ...toAdd]);
    }
  };

  const clearSelection = () => {
    setSelectedDocIds([]);
    setDossierWarning(null);
  };

  const handleExportDossier = async (title: string) => {
    setIsGeneratingDossier(true);
    setDossierError(null);
    try {
      const blob = await documentsApi.exportDossierDocx(selectedDocIds, title || undefined);
      const filename = (blob as any)?.filename || 'dossier-documental-hitchings.docx';
      triggerBrowserDownload(blob, filename);
      setIsDossierModalOpen(false);
      setDossierSuccess(`Dossier generado correctamente: ${filename}`);
      // Clear selection after successful download
      setSelectedDocIds([]);
      setTimeout(() => setDossierSuccess(null), 6000);
    } catch (err: unknown) {
      setDossierError(mapDossierExportErrorMessage(err));
    } finally {
      setIsGeneratingDossier(false);
    }
  };

  return (
    <div className="space-y-6 pb-24">
      {/* Header section */}
      <div className="bg-white rounded-lg border border-slate-200 p-6 shadow-sm">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-navy-900 mb-1">
              <FolderArchive className="w-6 h-6 text-legal-gold" />
              <h1 className="text-2xl font-serif font-bold">Fondo Documental</h1>
            </div>
            <p className="text-sm text-slate-600">
              Biblioteca documental completa de resoluciones, sentencias, artículos e informes capturados por el sistema.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold px-2.5 py-1 rounded bg-slate-100 text-slate-700 border border-slate-200">
              Total documentos: <strong className="text-navy-950 font-bold">{total}</strong>
            </span>
            <button
              type="button"
              onClick={() => setIsAddModalOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-navy-900 text-white rounded-md text-xs font-semibold hover:bg-navy-800 transition-colors shadow-sm"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Añadir documento</span>
            </button>
          </div>
        </div>

        {/* Filter bar */}
        <div className="mt-6 pt-5 border-t border-slate-100 flex flex-col md:flex-row gap-3">
          <form onSubmit={handleSearchSubmit} className="flex-1 relative">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Buscar por título, contenido, autor o tribunal..."
              className="w-full pl-9 pr-24 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:ring-1 focus:ring-navy-900 focus:border-navy-900 bg-white"
            />
            {searchInput && (
              <button
                type="button"
                onClick={() => {
                  setSearchInput('');
                  updateParam('q', null);
                }}
                className="absolute right-20 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 p-1"
                title="Limpiar búsqueda"
              >
                <X className="w-4 h-4" />
              </button>
            )}
            <button
              type="submit"
              className="absolute right-2 top-1/2 -translate-y-1/2 px-3 py-1 bg-navy-900 text-white text-xs font-medium rounded hover:bg-navy-800 transition-colors"
            >
              Buscar
            </button>
          </form>

          {/* Analysis status filter */}
          <select
            value={hasAnalysis}
            onChange={(e) => updateParam('has_analysis', e.target.value)}
            className="border border-slate-300 rounded-md px-3 py-2 text-sm bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-navy-900"
          >
            <option value="all">Todos los estados</option>
            <option value="with_analysis">Con análisis IA completado</option>
            <option value="without_analysis">Sin análisis (Archivo original)</option>
          </select>

          {/* Source filter */}
          <select
            value={sourceId}
            onChange={(e) => updateParam('source_id', e.target.value)}
            className="border border-slate-300 rounded-md px-3 py-2 text-sm bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-navy-900 max-w-xs"
          >
            <option value="">Todas las fuentes</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Dossier Notification Toasts */}
      {dossierSuccess && (
        <div
          className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-800 flex items-center justify-between shadow-sm animate-fadeIn"
          role="status"
        >
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0" />
            <span>{dossierSuccess}</span>
          </div>
          <button
            type="button"
            onClick={() => setDossierSuccess(null)}
            className="text-emerald-700 hover:text-emerald-900 font-bold ml-2"
            aria-label="Cerrar notificación"
          >
            ×
          </button>
        </div>
      )}

      {dossierWarning && (
        <div
          className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800 flex items-center justify-between shadow-sm animate-fadeIn"
          role="alert"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0" />
            <span>{dossierWarning}</span>
          </div>
          <button
            type="button"
            onClick={() => setDossierWarning(null)}
            className="text-amber-700 hover:text-amber-900 font-bold ml-2"
            aria-label="Cerrar aviso"
          >
            ×
          </button>
        </div>
      )}

      {/* Selection Toolbar (above list) */}
      {!loading && !error && documents.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 bg-slate-100/90 rounded-lg border border-slate-200 text-xs text-slate-700">
          <label className="inline-flex items-center gap-2 cursor-pointer select-none font-medium text-slate-800">
            <input
              type="checkbox"
              ref={masterCheckboxRef}
              checked={isAllInPageSelected}
              onChange={toggleSelectAllInPage}
              aria-label="Seleccionar todos los documentos en esta página"
              className="w-4 h-4 rounded border-slate-300 text-navy-900 focus:ring-navy-900 cursor-pointer"
            />
            <span>Seleccionar todos en esta página ({documents.length})</span>
          </label>

          <div className="flex items-center gap-3">
            {selectedDocIds.length > 0 ? (
              <>
                <span className="font-semibold text-navy-900">
                  {selectedDocIds.length} seleccionado{selectedDocIds.length === 1 ? '' : 's'} (máx. {MAX_DOSSIER_DOCUMENTS})
                </span>
                <button
                  type="button"
                  onClick={clearSelection}
                  className="text-slate-600 hover:text-navy-900 underline font-medium"
                >
                  Limpiar selección
                </button>
              </>
            ) : (
              <span className="text-slate-500 italic">
                Selecciona casillas para exportar un dossier conjunto (máx. 30)
              </span>
            )}
          </div>
        </div>
      )}

      {/* Main listing view */}
      {loading ? (
        <div className="space-y-4">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : error ? (
        <ErrorState message={error} onRetry={() => updateParam('offset', '0')} />
      ) : documents.length === 0 ? (
        <EmptyState
          title="No se encontraron documentos"
          message="No hay entradas documentales que coincidan con los filtros especificados."
          onClearFilters={clearAllFilters}
        />
      ) : (
        <div className="space-y-4">
          {documents.map((doc) => {
            const isLinkedin = doc.is_linkedin;
            const isSelected = selectedDocIds.includes(doc.id);
            const isLimitReached = !isSelected && selectedDocIds.length >= MAX_DOSSIER_DOCUMENTS;

            return (
              <article
                key={doc.id}
                className={`bg-white rounded-lg border p-5 shadow-sm transition-all group ${
                  isSelected
                    ? 'border-navy-900 ring-1 ring-navy-900/10 bg-slate-50/40'
                    : 'border-slate-200 hover:border-slate-300'
                }`}
              >
                <div className="flex items-start gap-3">
                  {/* Item Checkbox */}
                  <div className="pt-0.5 flex-shrink-0">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      disabled={isLimitReached}
                      onChange={() => toggleSelectDocument(doc.id)}
                      aria-label={`Seleccionar documento: ${formatDocumentTitle(doc.title)}`}
                      title={
                        isLimitReached
                          ? 'Límite máximo de 30 documentos alcanzado'
                          : 'Seleccionar para incluir en el dossier'
                      }
                      className="w-4 h-4 rounded border-slate-300 text-navy-900 focus:ring-navy-900 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    />
                  </div>

                  {/* Document Card Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-col sm:flex-row sm:items-baseline justify-between gap-2 mb-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded bg-slate-100 text-slate-700 border border-slate-200">
                          {isLinkedin ? <User className="w-3 h-3" /> : <Building2 className="w-3 h-3" />}
                          <span>{doc.source.name}</span>
                        </span>

                        {doc.has_analysis ? (
                          <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded bg-emerald-50 text-emerald-800 border border-emerald-200">
                            <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                            Analizado por IA
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">
                            <Clock className="w-3 h-3 text-slate-400" />
                            Archivo original
                          </span>
                        )}

                        {doc.source.tracked_entity_name && (
                          <span className="text-xs text-slate-500 font-medium">
                            Entidad: {doc.source.tracked_entity_name}
                          </span>
                        )}
                      </div>

                      {doc.published_at && (
                        <span className="text-xs text-slate-500 flex items-center gap-1">
                          <Calendar className="w-3.5 h-3.5" />
                          {new Date(doc.published_at).toLocaleDateString('es-ES', {
                            year: 'numeric',
                            month: 'short',
                            day: 'numeric',
                          })}
                        </span>
                      )}
                    </div>

                    {/* Title */}
                    <h2 className="text-base font-bold text-slate-900 group-hover:text-navy-900 mb-2">
                      <Link to={`/documentos/${doc.id}`} className="hover:underline">
                        {formatDocumentTitle(doc.title)}
                      </Link>
                    </h2>

                    {/* Excerpt / Summary */}
                    {doc.analysis?.summary ? (
                      <div className="mb-3 p-3 bg-amber-50/40 border-l-2 border-legal-gold rounded-r text-xs text-slate-700">
                        <span className="font-semibold text-navy-900 block mb-0.5">Resumen de Análisis:</span>
                        {doc.analysis.summary}
                      </div>
                    ) : doc.excerpt ? (
                      <p className="text-xs text-slate-600 mb-3 line-clamp-2">{doc.excerpt}</p>
                    ) : null}

                    {/* Badges / Topics */}
                    <div className="flex items-center justify-between gap-3 pt-3 border-t border-slate-100 flex-wrap">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {doc.analysis?.relevance_status && (
                          <RelevanceBadge
                            status={doc.analysis.relevance_status}
                            score={doc.analysis.relevance_score ?? 0}
                          />
                        )}
                        {doc.analysis?.canonical_topics.map((t) => (
                          <TopicBadge key={t.code} code={t.code} name={t.name} />
                        ))}
                      </div>

                      <div className="flex items-center gap-3">
                        <a
                          href={doc.canonical_url || doc.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-navy-900"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                          <span>Fuente original</span>
                        </a>
                        <Link
                          to={`/documentos/${doc.id}`}
                          className="text-xs font-semibold text-navy-900 hover:text-navy-950 px-2.5 py-1 bg-slate-100 hover:bg-slate-200 rounded transition-colors"
                        >
                          Ver expediente →
                        </Link>
                      </div>
                    </div>
                  </div>
                </div>
              </article>
            );
          })}

          {/* Pagination controls */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-4 border-t border-slate-200">
              <span className="text-xs text-slate-500">
                Página {currentPage} de {totalPages}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => updateParam('offset', String(Math.max(0, offset - limit)))}
                  className="px-3 py-1.5 border border-slate-300 rounded text-xs font-medium bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-1"
                >
                  <ChevronLeft className="w-3.5 h-3.5" /> Anterior
                </button>
                <button
                  type="button"
                  disabled={offset + limit >= total}
                  onClick={() => updateParam('offset', String(offset + limit))}
                  className="px-3 py-1.5 border border-slate-300 rounded text-xs font-medium bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-1"
                >
                  Siguiente <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Floating Action Bar (Sticky when selection > 0) */}
      {selectedDocIds.length > 0 && (
        <div
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-navy-950 text-white rounded-xl shadow-2xl border border-slate-700 px-5 py-3 flex items-center justify-between gap-4 max-w-2xl w-[92%] animate-fadeIn"
          role="region"
          aria-label="Acciones de documentos seleccionados"
        >
          <div className="flex items-center gap-2 sm:gap-3 min-w-0">
            <span className="text-xs sm:text-sm font-semibold truncate">
              {selectedDocIds.length} documento{selectedDocIds.length === 1 ? '' : 's'} seleccionado{selectedDocIds.length === 1 ? '' : 's'}
              <span className="text-slate-400 font-normal ml-1">(máx. {MAX_DOSSIER_DOCUMENTS})</span>
            </span>
            {selectedDocIds.length >= MAX_DOSSIER_DOCUMENTS && (
              <span className="hidden sm:inline-block px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-legal-gold border border-legal-gold/40">
                Límite 30 alcanzado
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 sm:gap-3 flex-shrink-0">
            <button
              type="button"
              onClick={clearSelection}
              disabled={isGeneratingDossier}
              className="px-2.5 py-1.5 text-xs text-slate-300 hover:text-white underline transition-colors disabled:opacity-50"
            >
              Limpiar selección
            </button>
            <button
              type="button"
              onClick={() => setIsDossierModalOpen(true)}
              disabled={isGeneratingDossier || selectedDocIds.length === 0}
              className="px-4 py-2 bg-legal-gold text-navy-950 hover:bg-amber-400 font-bold text-xs rounded-lg shadow transition-colors flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <FileDown className="w-4 h-4" />
              <span>Exportar Dossier Word</span>
            </button>
          </div>
        </div>
      )}

      {/* Modal Exportar Dossier Word */}
      <ExportDossierModal
        isOpen={isDossierModalOpen}
        onClose={() => setIsDossierModalOpen(false)}
        selectedCount={selectedDocIds.length}
        onConfirm={handleExportDossier}
        isGenerating={isGeneratingDossier}
        error={dossierError}
        onClearError={() => setDossierError(null)}
      />

      {/* Modal Añadir Documento */}
      <AddDocumentModal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        onDocumentAdded={() => {
          setRefreshTrigger((t) => t + 1);
        }}
      />
    </div>
  );
};
