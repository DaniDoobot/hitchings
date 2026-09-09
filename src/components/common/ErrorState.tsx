import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

export const ErrorState: React.FC<ErrorStateProps> = ({
  title = 'Ha ocurrido un error al cargar la información',
  message = 'No se ha podido conectar con el servicio del Observatorio o la consulta ha fallado.',
  onRetry,
}) => {
  return (
    <div className="bg-white rounded-lg border border-red-200 p-8 text-center max-w-lg mx-auto shadow-sm my-8">
      <div className="w-12 h-12 bg-red-50 text-red-600 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100">
        <AlertTriangle className="w-6 h-6" />
      </div>
      <h3 className="text-base font-semibold text-slate-900 mb-1">{title}</h3>
      <p className="text-sm text-slate-600 mb-6">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy-900 text-white rounded-md text-sm font-medium hover:bg-navy-800 transition-colors shadow-sm"
        >
          <RefreshCw className="w-4 h-4" />
          Reintentar
        </button>
      )}
    </div>
  );
};
