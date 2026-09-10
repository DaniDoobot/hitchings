import React, { useState, useEffect, useCallback } from 'react';
import {
  X,
  Plus,
  Edit2,
  Archive,
  RotateCcw,
  AlertTriangle,
  RefreshCw,
  Info,
  FolderPlus,
  Folder,
  Tag,
  Layers,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
} from 'lucide-react';
import {
  TaxonomyMatrixResponse,
  TaxonomyAreaNode,
  TaxonomyTopicItem,
} from '../../types/taxonomy';
import { taxonomyApi } from '../../services/taxonomyApi';

interface TaxonomyManagementModalProps {
  isOpen: boolean;
  onClose: () => void;
  onTaxonomyChanged?: () => void;
}

export const TaxonomyManagementModal: React.FC<TaxonomyManagementModalProps> = ({
  isOpen,
  onClose,
  onTaxonomyChanged,
}) => {
  const [taxonomy, setTaxonomy] = useState<TaxonomyMatrixResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [concurrencyConflict, setConcurrencyConflict] = useState<boolean>(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [collapsedAreas, setCollapsedAreas] = useState<Record<string, boolean>>({});
  const [hasMutated, setHasMutated] = useState<boolean>(false);

  // Sub-modal states
  const [areaModal, setAreaModal] = useState<{
    mode: 'create' | 'edit';
    areaId?: string;
    name: string;
    description: string;
  } | null>(null);

  const [topicModal, setTopicModal] = useState<{
    mode: 'create' | 'edit';
    topicId?: string;
    areaId: string;
    name: string;
    description: string;
  } | null>(null);

  const [actionLoading, setActionLoading] = useState<boolean>(false);

  const loadTaxonomy = useCallback(async () => {
    setLoading(true);
    setError(null);
    setConcurrencyConflict(false);
    try {
      const data = await taxonomyApi.getTaxonomy();
      setTaxonomy(data);
    } catch (err: any) {
      console.error('Failed to load taxonomy:', err);
      setError(err?.message || 'Error al cargar la taxonomía de seguimiento');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      loadTaxonomy();
      setSuccessMsg(null);
      setHasMutated(false);
    }
  }, [isOpen, loadTaxonomy]);

  const handleClose = () => {
    if (hasMutated && onTaxonomyChanged) {
      onTaxonomyChanged();
    }
    onClose();
  };

  const handleMutationSuccess = (newTax: TaxonomyMatrixResponse, message: string) => {
    setTaxonomy(newTax);
    setSuccessMsg(message);
    setConcurrencyConflict(false);
    setError(null);
    setHasMutated(true);
    setTimeout(() => setSuccessMsg(null), 4000);
  };

  const handleMutationError = (err: any) => {
    console.error('Taxonomy mutation error:', err);
    if (err?.status === 409 || err?.message?.includes('409') || err?.message?.includes('modificada')) {
      setConcurrencyConflict(true);
      setError(null);
    } else {
      setError(err?.message || 'Ocurrió un error al procesar la solicitud');
    }
  };

  // Toggle area collapse
  const toggleAreaCollapse = (areaId: string) => {
    setCollapsedAreas(prev => ({ ...prev, [areaId]: !prev[areaId] }));
  };

  // Submit Area Form
  const handleSubmitArea = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!taxonomy || !areaModal || !areaModal.name.trim()) return;

    setActionLoading(true);
    setError(null);
    try {
      if (areaModal.mode === 'create') {
        const res = await taxonomyApi.createArea({
          base_matrix_id: taxonomy.matrix_id,
          name: areaModal.name.trim(),
          description: areaModal.description.trim() || undefined,
        });
        handleMutationSuccess(res, `Área "${areaModal.name.trim()}" creada con éxito.`);
      } else if (areaModal.mode === 'edit' && areaModal.areaId) {
        const res = await taxonomyApi.updateArea(areaModal.areaId, {
          base_matrix_id: taxonomy.matrix_id,
          name: areaModal.name.trim(),
          description: areaModal.description.trim() || undefined,
        });
        handleMutationSuccess(res, `Área actualizada con éxito.`);
      }
      setAreaModal(null);
    } catch (err: any) {
      handleMutationError(err);
    } finally {
      setActionLoading(false);
    }
  };

  // Submit Topic Form
  const handleSubmitTopic = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!taxonomy || !topicModal || !topicModal.name.trim()) return;

    setActionLoading(true);
    setError(null);
    try {
      if (topicModal.mode === 'create') {
        const res = await taxonomyApi.createTopic({
          base_matrix_id: taxonomy.matrix_id,
          area_id: topicModal.areaId,
          name: topicModal.name.trim(),
          description: topicModal.description.trim() || undefined,
        });
        handleMutationSuccess(res, `Tema "${topicModal.name.trim()}" creado con éxito.`);
      } else if (topicModal.mode === 'edit' && topicModal.topicId) {
        const res = await taxonomyApi.updateTopic(topicModal.topicId, {
          base_matrix_id: taxonomy.matrix_id,
          area_id: topicModal.areaId,
          name: topicModal.name.trim(),
          description: topicModal.description.trim() || undefined,
        });
        handleMutationSuccess(res, `Tema actualizado con éxito.`);
      }
      setTopicModal(null);
    } catch (err: any) {
      handleMutationError(err);
    } finally {
      setActionLoading(false);
    }
  };

  // Area Status Toggle
  const handleToggleAreaStatus = async (area: TaxonomyAreaNode) => {
    if (!taxonomy) return;
    const isArchiving = area.active;
    const confirmMsg = isArchiving
      ? `¿Archivar el área "${area.name}" y todos sus temas asociados? Dejarán de clasificarse nuevas noticias en ella.`
      : `¿Reactivar el área "${area.name}"?`;
    if (!window.confirm(confirmMsg)) return;

    setActionLoading(true);
    try {
      const res = isArchiving
        ? await taxonomyApi.archiveArea(area.id, { base_matrix_id: taxonomy.matrix_id })
        : await taxonomyApi.reactivateArea(area.id, { base_matrix_id: taxonomy.matrix_id });
      handleMutationSuccess(res, `Área "${area.name}" ${isArchiving ? 'archivada' : 'reactivada'}.`);
    } catch (err: any) {
      handleMutationError(err);
    } finally {
      setActionLoading(false);
    }
  };

  // Topic Status Toggle
  const handleToggleTopicStatus = async (topic: TaxonomyTopicItem) => {
    if (!taxonomy) return;
    const isArchiving = topic.active;
    const confirmMsg = isArchiving
      ? `¿Archivar el tema "${topic.name}"? Dejará de asociarse a nuevos análisis.`
      : `¿Reactivar el tema "${topic.name}"?`;
    if (!window.confirm(confirmMsg)) return;

    setActionLoading(true);
    try {
      const res = isArchiving
        ? await taxonomyApi.archiveTopic(topic.id, { base_matrix_id: taxonomy.matrix_id })
        : await taxonomyApi.reactivateTopic(topic.id, { base_matrix_id: taxonomy.matrix_id });
      handleMutationSuccess(res, `Tema "${topic.name}" ${isArchiving ? 'archivado' : 'reactivado'}.`);
    } catch (err: any) {
      handleMutationError(err);
    } finally {
      setActionLoading(false);
    }
  };

  if (!isOpen) return null;

  // Filter areas and topics based on search & archived toggle
  const q = searchQuery.toLowerCase().trim();
  const filteredAreas = (taxonomy?.areas || [])
    .map(area => {
      const matchesArea = area.name.toLowerCase().includes(q) || area.code.toLowerCase().includes(q);
      const matchingChildren = area.children.filter(topic => {
        if (!showArchived && !topic.active) return false;
        if (!q) return true;
        return (
          matchesArea ||
          topic.name.toLowerCase().includes(q) ||
          topic.code.toLowerCase().includes(q)
        );
      });

      if (!showArchived && !area.active && matchingChildren.length === 0) {
        return null;
      }

      if (q && !matchesArea && matchingChildren.length === 0) {
        return null;
      }

      return {
        ...area,
        children: matchingChildren,
      };
    })
    .filter((a): a is TaxonomyAreaNode => a !== null);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden animate-fadeIn">
        {/* Header */}
        <div className="px-6 py-4 bg-slate-900 text-white flex items-center justify-between border-b border-slate-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-slate-800 rounded-lg text-legal-gold">
              <Layers className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold tracking-wide">Gestión de Áreas y Temas</h2>
                {taxonomy && (
                  <span className="px-2 py-0.5 text-xs font-mono font-semibold bg-legal-gold/20 text-legal-gold border border-legal-gold/30 rounded">
                    {taxonomy.code}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400">
                Taxonomía jurídica de seguimiento del Observatorio HITCHINGS Y GONZALEZ
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors"
            title="Cerrar"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Institutional Disclaimer */}
        <div className="bg-amber-50/90 border-b border-amber-200/80 px-6 py-3">
          <div className="flex items-start gap-2.5 text-xs text-amber-900">
            <Info className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
            <div>
              <strong className="font-semibold text-amber-950">Aviso institucional:</strong> Los cambios se aplicarán a futuros análisis. Las publicaciones ya analizadas no se reevaluarán automáticamente ni generarán llamadas a modelos externos.
            </div>
          </div>
        </div>

        {/* Feedback Messages */}
        {concurrencyConflict && (
          <div className="bg-red-50 border-b border-red-200 px-6 py-3 flex items-center justify-between gap-4">
            <div className="flex items-center gap-2 text-xs font-medium text-red-800">
              <AlertTriangle className="w-4 h-4 text-red-600 shrink-0" />
              <span>
                La configuración ha sido modificada por otro usuario o sesión. Por favor, recargue la configuración para continuar.
              </span>
            </div>
            <button
              onClick={loadTaxonomy}
              className="px-3 py-1 bg-red-100 hover:bg-red-200 text-red-800 rounded text-xs font-semibold flex items-center gap-1.5 transition-colors shrink-0"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Recargar taxonomía
            </button>
          </div>
        )}

        {error && !concurrencyConflict && (
          <div className="bg-red-50 border-b border-red-200 px-6 py-3 flex items-center gap-2 text-xs font-medium text-red-800">
            <AlertTriangle className="w-4 h-4 text-red-600 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {successMsg && (
          <div className="bg-emerald-50 border-b border-emerald-200 px-6 py-2.5 flex items-center gap-2 text-xs font-medium text-emerald-800">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
            <span>{successMsg}</span>
          </div>
        )}

        {/* Controls Toolbar */}
        <div className="px-6 py-3 bg-slate-50 border-b border-slate-200 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-4 flex-1 min-w-[240px]">
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Filtrar áreas o temas..."
              className="w-full max-w-xs px-3 py-1.5 text-xs bg-white border border-slate-300 rounded-md text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-navy-700"
            />
            <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={showArchived}
                onChange={e => setShowArchived(e.target.checked)}
                className="rounded text-navy-700 focus:ring-navy-700 border-slate-300 w-3.5 h-3.5"
              />
              <span>Mostrar archivados</span>
            </label>
          </div>

          <button
            onClick={() => setAreaModal({ mode: 'create', name: '', description: '' })}
            disabled={loading || actionLoading}
            className="px-3 py-1.5 bg-navy-800 hover:bg-navy-900 text-white rounded-md text-xs font-semibold flex items-center gap-1.5 transition-colors shadow-sm disabled:opacity-50"
          >
            <Plus className="w-3.5 h-3.5 text-legal-gold" />
            Nueva Área
          </button>
        </div>

        {/* Main Content / Hierarchy Tree */}
        <div className="p-6 overflow-y-auto flex-1 space-y-4 bg-slate-50/50">
          {loading ? (
            <div className="py-12 flex flex-col items-center justify-center text-slate-500 gap-2">
              <RefreshCw className="w-6 h-6 animate-spin text-navy-700" />
              <span className="text-xs">Cargando taxonomía...</span>
            </div>
          ) : filteredAreas.length === 0 ? (
            <div className="py-12 text-center text-slate-500 bg-white rounded-lg border border-slate-200 p-8">
              <Folder className="w-8 h-8 mx-auto text-slate-400 mb-2" />
              <p className="text-sm font-semibold text-slate-700">No se encontraron áreas ni temas</p>
              <p className="text-xs text-slate-400 mt-1">Pruebe ajustando los filtros o cree una nueva área.</p>
            </div>
          ) : (
            filteredAreas.map(area => {
              const isCollapsed = !!collapsedAreas[area.id];
              return (
                <div
                  key={area.id}
                  className={`bg-white rounded-lg border transition-all shadow-sm ${
                    area.active ? 'border-slate-200' : 'border-slate-200 bg-slate-100/50 opacity-80'
                  }`}
                >
                  {/* Area Row */}
                  <div className="px-4 py-3 flex items-center justify-between border-b border-slate-100">
                    <div className="flex items-center gap-2.5 flex-1 min-w-0">
                      <button
                        onClick={() => toggleAreaCollapse(area.id)}
                        className="p-1 text-slate-400 hover:text-slate-700 rounded transition-colors"
                        title={isCollapsed ? 'Expandir temas' : 'Colapsar temas'}
                      >
                        {isCollapsed ? (
                          <ChevronRight className="w-4 h-4" />
                        ) : (
                          <ChevronDown className="w-4 h-4" />
                        )}
                      </button>
                      <Folder className={`w-4 h-4 shrink-0 ${area.active ? 'text-navy-700' : 'text-slate-400'}`} />
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-bold text-sm text-slate-900">{area.name}</span>
                          <span className="text-[11px] font-mono text-slate-400 bg-slate-50 px-1.5 py-0.2 rounded border border-slate-200">
                            {area.code}
                          </span>
                          <span
                            className={`text-[10px] uppercase font-semibold px-2 py-0.5 rounded-full ${
                              area.active
                                ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                : 'bg-slate-100 text-slate-500 border border-slate-200'
                            }`}
                          >
                            {area.active ? 'Activo' : 'Archivado'}
                          </span>
                        </div>
                        {area.description && (
                          <p className="text-xs text-slate-500 mt-0.5 truncate">{area.description}</p>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-1 shrink-0 ml-3">
                      {area.active && (
                        <button
                          onClick={() =>
                            setTopicModal({
                              mode: 'create',
                              areaId: area.id,
                              name: '',
                              description: '',
                            })
                          }
                          disabled={actionLoading}
                          className="px-2.5 py-1 text-xs text-navy-800 hover:bg-slate-100 rounded font-medium flex items-center gap-1 transition-colors"
                          title="Añadir Tema a esta Área"
                        >
                          <Plus className="w-3.5 h-3.5 text-legal-gold" />
                          Tema
                        </button>
                      )}
                      <button
                        onClick={() =>
                          setAreaModal({
                            mode: 'edit',
                            areaId: area.id,
                            name: area.name,
                            description: area.description || '',
                          })
                        }
                        disabled={actionLoading}
                        className="p-1.5 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded transition-colors"
                        title="Editar Área"
                      >
                        <Edit2 className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => handleToggleAreaStatus(area)}
                        disabled={actionLoading}
                        className={`p-1.5 rounded transition-colors ${
                          area.active
                            ? 'text-slate-400 hover:text-red-600 hover:bg-red-50'
                            : 'text-slate-400 hover:text-emerald-600 hover:bg-emerald-50'
                        }`}
                        title={area.active ? 'Archivar Área' : 'Reactivar Área'}
                      >
                        {area.active ? <Archive className="w-3.5 h-3.5" /> : <RotateCcw className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>

                  {/* Child Temas List */}
                  {!isCollapsed && (
                    <div className="p-3 bg-slate-50/50 space-y-2">
                      {area.children.length === 0 ? (
                        <div className="py-2 px-3 text-xs text-slate-400 italic">
                          No hay temas configurados en esta área.
                        </div>
                      ) : (
                        area.children.map(topic => (
                          <div
                            key={topic.id}
                            className={`flex items-center justify-between p-2.5 rounded-md border bg-white transition-all ${
                              topic.active ? 'border-slate-200' : 'border-slate-200 bg-slate-100 opacity-70'
                            }`}
                          >
                            <div className="flex items-center gap-2.5 min-w-0">
                              <Tag className={`w-3.5 h-3.5 shrink-0 ${topic.active ? 'text-slate-500' : 'text-slate-400'}`} />
                              <div className="min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="text-xs font-semibold text-slate-800">{topic.name}</span>
                                  <span className="text-[10px] font-mono text-slate-400 bg-slate-50 px-1 py-0.2 rounded border border-slate-200">
                                    {topic.code}
                                  </span>
                                  <span
                                    className={`text-[9px] uppercase font-semibold px-1.5 py-0.2 rounded ${
                                      topic.active
                                        ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                        : 'bg-slate-100 text-slate-400 border border-slate-200'
                                    }`}
                                  >
                                    {topic.active ? 'Activo' : 'Archivado'}
                                  </span>
                                </div>
                                {topic.description && (
                                  <p className="text-[11px] text-slate-500 mt-0.5 truncate">{topic.description}</p>
                                )}
                              </div>
                            </div>

                            <div className="flex items-center gap-1 shrink-0 ml-2">
                              <button
                                onClick={() =>
                                  setTopicModal({
                                    mode: 'edit',
                                    topicId: topic.id,
                                    areaId: area.id,
                                    name: topic.name,
                                    description: topic.description || '',
                                  })
                                }
                                disabled={actionLoading}
                                className="p-1 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded transition-colors"
                                title="Editar Tema o reubicar de Área"
                              >
                                <Edit2 className="w-3 h-3" />
                              </button>
                              <button
                                onClick={() => handleToggleTopicStatus(topic)}
                                disabled={actionLoading}
                                className={`p-1 rounded transition-colors ${
                                  topic.active
                                    ? 'text-slate-400 hover:text-red-600 hover:bg-red-50'
                                    : 'text-slate-400 hover:text-emerald-600 hover:bg-emerald-50'
                                }`}
                                title={topic.active ? 'Archivar Tema' : 'Reactivar Tema'}
                              >
                                {topic.active ? <Archive className="w-3 h-3" /> : <RotateCcw className="w-3 h-3" />}
                              </button>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 bg-white border-t border-slate-200 flex items-center justify-between text-xs text-slate-500">
          <span>
            {taxonomy ? `${taxonomy.areas.length} áreas configuradas` : ''}
          </span>
          <button
            onClick={handleClose}
            className="px-4 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-800 rounded-md font-semibold transition-colors"
          >
            Cerrar
          </button>
        </div>

        {/* Modal: Create/Edit Area */}
        {areaModal && (
          <div className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
            <div className="bg-white rounded-lg shadow-xl border border-slate-200 w-full max-w-md p-6 animate-scaleIn">
              <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
                <FolderPlus className="w-4 h-4 text-navy-700" />
                {areaModal.mode === 'create' ? 'Nueva Área Jurídica' : 'Editar Área'}
              </h3>
              <form onSubmit={handleSubmitArea} className="space-y-4 text-xs">
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Nombre del Área *</label>
                  <input
                    type="text"
                    required
                    value={areaModal.name}
                    onChange={e => setAreaModal({ ...areaModal, name: e.target.value })}
                    placeholder="ej: Control de Ayudas de Estado"
                    className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-1 focus:ring-navy-700"
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Descripción (opcional)</label>
                  <textarea
                    rows={3}
                    value={areaModal.description}
                    onChange={e => setAreaModal({ ...areaModal, description: e.target.value })}
                    placeholder="Descripción y alcance de esta área..."
                    className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-1 focus:ring-navy-700"
                  />
                </div>
                <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100">
                  <button
                    type="button"
                    onClick={() => setAreaModal(null)}
                    disabled={actionLoading}
                    className="px-3 py-1.5 text-slate-600 hover:bg-slate-100 rounded font-medium transition-colors"
                  >
                    Cancelar
                  </button>
                  <button
                    type="submit"
                    disabled={actionLoading || !areaModal.name.trim()}
                    className="px-4 py-1.5 bg-navy-800 hover:bg-navy-900 text-white rounded font-semibold transition-colors disabled:opacity-50"
                  >
                    {actionLoading ? 'Guardando...' : areaModal.mode === 'create' ? 'Crear Área' : 'Guardar Cambios'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Modal: Create/Edit Topic */}
        {topicModal && (
          <div className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
            <div className="bg-white rounded-lg shadow-xl border border-slate-200 w-full max-w-md p-6 animate-scaleIn">
              <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
                <Tag className="w-4 h-4 text-navy-700" />
                {topicModal.mode === 'create' ? 'Nuevo Tema / Subtema' : 'Editar Tema'}
              </h3>
              <form onSubmit={handleSubmitTopic} className="space-y-4 text-xs">
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Área Perteneciente *</label>
                  <select
                    value={topicModal.areaId}
                    onChange={e => setTopicModal({ ...topicModal, areaId: e.target.value })}
                    className="w-full px-3 py-2 border border-slate-300 rounded-md bg-white focus:outline-none focus:ring-1 focus:ring-navy-700"
                  >
                    {(taxonomy?.areas || [])
                      .filter(a => a.active || a.id === topicModal.areaId)
                      .map(a => (
                        <option key={a.id} value={a.id}>
                          {a.name} {!a.active ? '(Archivada)' : ''}
                        </option>
                      ))}
                  </select>
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Nombre del Tema *</label>
                  <input
                    type="text"
                    required
                    value={topicModal.name}
                    onChange={e => setTopicModal({ ...topicModal, name: e.target.value })}
                    placeholder="ej: Subvenciones Extranjeras (FSR)"
                    className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-1 focus:ring-navy-700"
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Descripción (opcional)</label>
                  <textarea
                    rows={3}
                    value={topicModal.description}
                    onChange={e => setTopicModal({ ...topicModal, description: e.target.value })}
                    placeholder="Descripción y conceptos clave de este tema..."
                    className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-1 focus:ring-navy-700"
                  />
                </div>
                <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100">
                  <button
                    type="button"
                    onClick={() => setTopicModal(null)}
                    disabled={actionLoading}
                    className="px-3 py-1.5 text-slate-600 hover:bg-slate-100 rounded font-medium transition-colors"
                  >
                    Cancelar
                  </button>
                  <button
                    type="submit"
                    disabled={actionLoading || !topicModal.name.trim()}
                    className="px-4 py-1.5 bg-navy-800 hover:bg-navy-900 text-white rounded font-semibold transition-colors disabled:opacity-50"
                  >
                    {actionLoading ? 'Guardando...' : topicModal.mode === 'create' ? 'Crear Tema' : 'Guardar Cambios'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
