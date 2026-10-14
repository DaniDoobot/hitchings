import React, { useState, useEffect, useRef } from 'react';
import { X, FileDown, AlertTriangle, Loader2 } from 'lucide-react';

interface ExportDossierModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedCount: number;
  onConfirm: (title: string) => Promise<void>;
  isGenerating: boolean;
  error: string | null;
  onClearError: () => void;
}

const DEFAULT_SUGGESTED_TITLE = 'Dossier documental HITCHINGS';

export const ExportDossierModal: React.FC<ExportDossierModalProps> = ({
  isOpen,
  onClose,
  selectedCount,
  onConfirm,
  isGenerating,
  error,
  onClearError,
}) => {
  const [title, setTitle] = useState(DEFAULT_SUGGESTED_TITLE);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setTitle(DEFAULT_SUGGESTED_TITLE);
      onClearError();
      setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }, 50);
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !isGenerating) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, isGenerating, onClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isGenerating) return;
    await onConfirm(title.trim());
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="export-dossier-modal-title"
    >
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-lg flex flex-col overflow-hidden animate-fadeIn">
        {/* Header */}
        <div className="px-6 py-4 bg-slate-900 text-white flex items-center justify-between border-b border-slate-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-slate-800 rounded-lg text-legal-gold">
              <FileDown className="w-5 h-5" />
            </div>
            <div>
              <h2 id="export-dossier-modal-title" className="text-base font-bold tracking-wide">
                Exportar Dossier Word
              </h2>
              <p className="text-xs text-slate-400">
                Compilación temática de {selectedCount} expediente{selectedCount === 1 ? '' : 's'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isGenerating}
            className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors disabled:opacity-50"
            title="Cerrar modal"
            aria-label="Cerrar modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <p className="text-xs text-slate-600 leading-relaxed">
            Se generará un documento Microsoft Word (.docx) formal con portada institucional,
            índice estructurado y capítulos independientes para los{' '}
            <strong>{selectedCount} documentos</strong> seleccionados.
          </p>

          {error && (
            <div
              className="p-3 bg-red-50 border border-red-200 rounded-lg text-xs text-red-800 flex items-start gap-2.5"
              role="alert"
            >
              <AlertTriangle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <span className="font-semibold block mb-0.5">Error al exportar:</span>
                {error}
              </div>
            </div>
          )}

          <div>
            <label
              htmlFor="dossier-title-input"
              className="block text-xs font-semibold text-slate-700 mb-1"
            >
              Título del dossier:
            </label>
            <input
              ref={inputRef}
              id="dossier-title-input"
              type="text"
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                if (error) onClearError();
              }}
              disabled={isGenerating}
              maxLength={200}
              placeholder="Dossier documental HITCHINGS"
              className="w-full px-3.5 py-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-800 focus:outline-none focus:ring-2 focus:ring-navy-900 focus:border-navy-900 disabled:bg-slate-100 disabled:text-slate-400"
            />
            <p className="text-[11px] text-slate-500 mt-1">
              El título es opcional. Si lo dejas vacío se utilizará el título institucional por defecto.
            </p>
          </div>

          {/* Footer Buttons */}
          <div className="pt-3 border-t border-slate-100 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={isGenerating}
              className="px-4 py-2 border border-slate-300 rounded-lg text-xs font-semibold text-slate-700 hover:bg-slate-50 transition-colors disabled:opacity-50"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={isGenerating || selectedCount === 0}
              className="px-5 py-2 bg-navy-900 text-white rounded-lg text-xs font-semibold hover:bg-navy-800 transition-colors flex items-center gap-2 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isGenerating ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Generando dossier...</span>
                </>
              ) : (
                <>
                  <FileDown className="w-3.5 h-3.5 text-legal-gold" />
                  <span>Generar Dossier Word</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
