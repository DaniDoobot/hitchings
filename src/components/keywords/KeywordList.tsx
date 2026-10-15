import React, { useState } from 'react';
import { Plus, Edit2, Trash2, AlertTriangle, Tag, Hash } from 'lucide-react';
import { TrackingTopic } from '../../types/tracking';
import { KeywordEditor } from './KeywordEditor';

interface KeywordListProps {
  topic: TrackingTopic;
  keywords: string[];
  searchQuery?: string;
  onAddKeyword: (newKeyword: string) => Promise<boolean>;
  onUpdateKeyword: (oldKeyword: string, newKeyword: string, index: number) => Promise<boolean>;
  onDeleteKeyword: (keyword: string, index: number) => Promise<boolean>;
  isSaving: boolean;
}

export const KeywordList: React.FC<KeywordListProps> = ({
  topic,
  keywords,
  searchQuery = '',
  onAddKeyword,
  onUpdateKeyword,
  onDeleteKeyword,
  isSaving,
}) => {
  const [isAdding, setIsAdding] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [deletingIndex, setDeletingIndex] = useState<number | null>(null);

  const trimmedQuery = searchQuery.trim().toLowerCase();

  const handleSaveNew = async (val: string) => {
    const success = await onAddKeyword(val);
    if (success) {
      setIsAdding(false);
    }
  };

  const handleSaveEdit = async (val: string, index: number) => {
    const oldKw = keywords[index];
    const success = await onUpdateKeyword(oldKw, val, index);
    if (success) {
      setEditingIndex(null);
    }
  };

  const handleConfirmDelete = async (kw: string, index: number) => {
    try {
      await onDeleteKeyword(kw, index);
    } finally {
      setDeletingIndex(null);
    }
  };

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm flex flex-col h-full">
      {/* Topic Header details */}
      <div className="p-5 border-b border-slate-200 bg-slate-50/50 rounded-t-lg">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded bg-navy-100 text-navy-800 border border-navy-200">
                {topic.code}
              </span>
              <span
                className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded ${
                  topic.active
                    ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                    : 'bg-slate-100 text-slate-500 border border-slate-200'
                }`}
              >
                {topic.active ? 'Activo' : 'Inactivo'}
              </span>
              {topic.priority > 0 && (
                <span className="text-[10px] font-medium text-slate-500">
                  Prioridad: <strong className="text-slate-700">{topic.priority}</strong>
                </span>
              )}
            </div>
            <h2 className="text-lg font-bold text-slate-900 font-serif">{topic.name}</h2>
            {topic.description && (
              <p className="text-xs text-slate-600 mt-1 max-w-2xl leading-relaxed">{topic.description}</p>
            )}
          </div>

          <div className="flex items-center gap-2 self-start sm:self-center">
            {!isAdding && (
              <button
                type="button"
                onClick={() => {
                  setIsAdding(true);
                  setEditingIndex(null);
                  setDeletingIndex(null);
                }}
                disabled={isSaving}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-navy-900 hover:bg-navy-950 text-white text-xs font-semibold rounded-md shadow-xs transition-colors cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5 text-legal-gold" />
                <span>+ Añadir palabra clave</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Keywords Container */}
      <div className="p-5 flex-1 flex flex-col">
        {/* Inline Add Form */}
        {isAdding && (
          <div className="mb-4 p-3.5 rounded-lg bg-navy-50/60 border border-navy-200/80 shadow-xs">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-navy-900 mb-2">
              <Plus className="w-3.5 h-3.5 text-legal-gold" />
              <span>Añadir nueva palabra clave a "{topic.name}"</span>
            </div>
            <KeywordEditor
              mode="add"
              existingKeywords={keywords}
              isSaving={isSaving}
              onSave={handleSaveNew}
              onCancel={() => setIsAdding(false)}
            />
          </div>
        )}

        {/* Counter & Subtitle */}
        <div className="flex items-center justify-between mb-3 text-xs text-slate-500">
          <span className="font-medium flex items-center gap-1.5">
            <Tag className="w-3.5 h-3.5 text-navy-600" />
            <span>Palabras clave configuradas</span>
            <span className="font-semibold text-slate-800">({keywords.length})</span>
          </span>
          {trimmedQuery && (
            <span className="text-[11px] text-navy-600 font-medium">
              Filtro activo: "{trimmedQuery}"
            </span>
          )}
        </div>

        {/* Keywords List */}
        {keywords.length === 0 ? (
          <div className="text-center py-10 px-4 my-auto border border-dashed border-slate-200 rounded-lg">
            <Hash className="w-8 h-8 text-slate-300 mx-auto mb-2" />
            <p className="text-sm font-semibold text-slate-700">No hay palabras clave configuradas</p>
            <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
              Este tema todavía no contiene señales o términos de búsqueda específicos.
            </p>
            {!isAdding && (
              <button
                type="button"
                onClick={() => {
                  setIsAdding(true);
                  setEditingIndex(null);
                  setDeletingIndex(null);
                }}
                className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-800 text-xs font-semibold rounded-md border border-slate-300 transition-colors"
              >
                <Plus className="w-3.5 h-3.5 text-navy-700" />
                <span>Añadir la primera palabra clave</span>
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {keywords.map((kw, idx) => {
              const isEditingThis = editingIndex === idx;
              const matchesQuery = !trimmedQuery || kw.toLowerCase().includes(trimmedQuery);

              if (!matchesQuery) {
                return null;
              }

              if (isEditingThis) {
                return (
                  <div
                    key={`edit-${idx}`}
                    className="p-3 rounded-lg bg-amber-50/60 border border-amber-200 shadow-xs"
                  >
                    <div className="text-[11px] font-semibold text-amber-900 mb-1.5 flex items-center gap-1">
                      <Edit2 className="w-3 h-3 text-amber-700" />
                      <span>Modificando palabra clave:</span>
                    </div>
                    <KeywordEditor
                      mode="edit"
                      initialValue={kw}
                      existingKeywords={keywords}
                      isSaving={isSaving}
                      onSave={(val) => handleSaveEdit(val, idx)}
                      onCancel={() => setEditingIndex(null)}
                    />
                  </div>
                );
              }

              if (deletingIndex === idx) {
                return (
                  <div
                    key={`delete-${idx}-${kw}`}
                    className="p-3 rounded-lg bg-rose-50/80 border border-rose-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs"
                  >
                    <div className="flex items-center gap-2 text-xs text-rose-950 font-medium min-w-0">
                      <AlertTriangle className="w-4 h-4 text-rose-600 flex-shrink-0" />
                      <span className="truncate">
                        ¿Eliminar la palabra clave <strong className="font-mono text-rose-900">"{kw}"</strong>?
                      </span>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 self-end sm:self-auto">
                      <button
                        type="button"
                        onClick={() => setDeletingIndex(null)}
                        disabled={isSaving}
                        className="px-2.5 py-1 text-xs font-medium text-slate-600 hover:text-slate-800 bg-white border border-slate-300 rounded hover:bg-slate-50 transition-colors cursor-pointer"
                      >
                        Cancelar
                      </button>
                      <button
                        type="button"
                        onClick={() => handleConfirmDelete(kw, idx)}
                        disabled={isSaving}
                        className="px-2.5 py-1 text-xs font-semibold text-white bg-rose-600 hover:bg-rose-700 rounded transition-colors shadow-xs cursor-pointer flex items-center gap-1"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        <span>{isSaving ? 'Eliminando...' : 'Eliminar'}</span>
                      </button>
                    </div>
                  </div>
                );
              }

              return (
                <div
                  key={`kw-${idx}-${kw}`}
                  className="flex items-center justify-between px-3.5 py-2.5 rounded-lg border border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50 transition-colors group"
                >
                  <div className="flex items-center gap-2.5 min-w-0 pr-3">
                    <span className="w-1.5 h-1.5 rounded-full bg-legal-gold flex-shrink-0" />
                    <span className="text-sm font-medium text-slate-900 font-mono break-words select-text">
                      {kw}
                    </span>
                  </div>

                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      type="button"
                      onClick={() => {
                        setEditingIndex(idx);
                        setIsAdding(false);
                        setDeletingIndex(null);
                      }}
                      disabled={isSaving}
                      className="p-1.5 rounded text-slate-400 hover:text-navy-900 hover:bg-slate-100 transition-colors cursor-pointer"
                      title="Editar palabra clave"
                      aria-label={`Editar ${kw}`}
                    >
                      <Edit2 className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setDeletingIndex(idx);
                        setEditingIndex(null);
                        setIsAdding(false);
                      }}
                      disabled={isSaving}
                      className="p-1.5 rounded text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer"
                      title="Eliminar palabra clave"
                      aria-label={`Eliminar ${kw}`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              );
            })}

            {/* If all filtered out */}
            {trimmedQuery &&
              !keywords.some((kw) => kw.toLowerCase().includes(trimmedQuery)) && (
                <div className="text-center py-6 text-xs text-slate-400">
                  Ninguna palabra clave coincide con el filtro en este tema.
                </div>
              )}
          </div>
        )}
      </div>
    </div>
  );
};
