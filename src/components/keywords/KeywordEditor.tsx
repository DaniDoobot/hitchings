import React, { useState, useEffect, useRef } from 'react';
import { Check, X, Loader2 } from 'lucide-react';

interface KeywordEditorProps {
  initialValue?: string;
  existingKeywords: string[];
  isSaving?: boolean;
  onSave: (value: string) => Promise<void> | void;
  onCancel: () => void;
  mode: 'add' | 'edit';
}

export const KeywordEditor: React.FC<KeywordEditorProps> = ({
  initialValue = '',
  existingKeywords,
  isSaving = false,
  onSave,
  onCancel,
  mode,
}) => {
  const [value, setValue] = useState(initialValue);
  const [validationError, setValidationError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setValue(initialValue);
    setValidationError(null);
  }, [initialValue]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) {
      e.preventDefault();
    }
    const trimmed = value.trim();

    if (!trimmed) {
      setValidationError('La palabra clave no puede estar vacía.');
      return;
    }

    // Check for duplicates (case-insensitive comparison, but exclude current initial value when in edit mode)
    const isDuplicate = existingKeywords.some((kw) => {
      if (mode === 'edit' && kw.toLowerCase() === initialValue.trim().toLowerCase()) {
        return false;
      }
      return kw.toLowerCase() === trimmed.toLowerCase();
    });

    if (isDuplicate) {
      setValidationError('Esta palabra clave ya existe en este tema.');
      return;
    }

    setValidationError(null);
    await onSave(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    }
  };

  return (
    <form onSubmit={handleSubmit} className="w-full">
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
        <div className="relative flex-1">
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              if (validationError) setValidationError(null);
            }}
            onKeyDown={handleKeyDown}
            disabled={isSaving}
            placeholder={mode === 'add' ? 'Escribir nueva palabra clave...' : 'Editar palabra clave...'}
            className={`w-full px-3 py-1.5 text-sm rounded border ${
              validationError
                ? 'border-red-400 focus:ring-red-300'
                : 'border-slate-300 focus:border-navy-700 focus:ring-navy-200'
            } bg-white text-slate-900 focus:outline-none focus:ring-2 shadow-xs transition-colors`}
          />
        </div>

        <div className="flex items-center gap-1.5 self-end sm:self-auto">
          <button
            type="submit"
            disabled={isSaving}
            className="inline-flex items-center gap-1 px-3 py-1.5 bg-navy-900 hover:bg-navy-950 text-white text-xs font-semibold rounded shadow-xs transition-colors disabled:opacity-50 cursor-pointer"
            title="Guardar"
          >
            {isSaving ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Guardando...</span>
              </>
            ) : (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-400" />
                <span>Guardar</span>
              </>
            )}
          </button>

          <button
            type="button"
            onClick={onCancel}
            disabled={isSaving}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-medium rounded border border-slate-200 transition-colors disabled:opacity-50 cursor-pointer"
            title="Cancelar"
          >
            <X className="w-3.5 h-3.5" />
            <span>Cancelar</span>
          </button>
        </div>
      </div>

      {validationError && (
        <p className="text-xs text-red-600 mt-1.5 font-medium flex items-center gap-1">
          <span>{validationError}</span>
        </p>
      )}
    </form>
  );
};
