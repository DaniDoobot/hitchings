import React from 'react';
import { SearchX } from 'lucide-react';

interface EmptyStateProps {
  title?: string;
  message?: string;
  onClearFilters?: () => void;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title = 'No hay publicaciones que coincidan con estos filtros.',
  message = 'Intente ajustar los términos de búsqueda o relajar los filtros aplicados (fuente, temas o fecha).',
  onClearFilters,
}) => {
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-8 sm:p-12 text-center shadow-sm my-6">
      <div className="w-12 h-12 bg-slate-100 text-slate-400 rounded-full flex items-center justify-center mx-auto mb-4">
        <SearchX className="w-6 h-6" aria-hidden="true" />
      </div>
      <h3 className="text-base font-semibold text-slate-800 mb-1">{title}</h3>
      <p className="text-sm text-slate-500 max-w-md mx-auto mb-6">{message}</p>
      {onClearFilters && (
        <button
          type="button"
          onClick={onClearFilters}
          className="inline-flex items-center px-4 py-2 bg-navy-900 hover:bg-navy-800 text-white rounded-md text-sm font-semibold shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-navy-700 focus:ring-offset-2"
        >
          Restablecer filtros
        </button>
      )}
    </div>
  );
};
