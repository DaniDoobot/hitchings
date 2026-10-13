import React, { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
  X,
  Globe,
  UploadCloud,
  FileText,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Sparkles,
  ExternalLink,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { documentsApi, ApiError } from '../../services/documentsApi';
import {
  DocumentDuplicateResponse,
  DocumentIngestionResponse,
} from '../../types/document';

interface AddDocumentModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDocumentAdded?: (documentId: string) => void;
}

const MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB
const ALLOWED_EXTENSIONS = ['.pdf', '.txt', '.md'];

export const AddDocumentModal: React.FC<AddDocumentModalProps> = ({
  isOpen,
  onClose,
  onDocumentAdded,
}) => {
  const [activeTab, setActiveTab] = useState<'url' | 'file'>('url');

  // URL mode state
  const [url, setUrl] = useState('');
  const [urlAnalyzeAi, setUrlAnalyzeAi] = useState(true);
  const [urlManualTitle, setUrlManualTitle] = useState('');
  const [urlManualAuthor, setUrlManualAuthor] = useState('');
  const [urlManualDate, setUrlManualDate] = useState('');
  const [urlShowAdvanced, setUrlShowAdvanced] = useState(false);

  // File mode state
  const [file, setFile] = useState<File | null>(null);
  const [fileAnalyzeAi, setFileAnalyzeAi] = useState(true);
  const [fileManualTitle, setFileManualTitle] = useState('');
  const [fileManualAuthor, setFileManualAuthor] = useState('');
  const [fileManualDate, setFileManualDate] = useState('');
  const [fileShowAdvanced, setFileShowAdvanced] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Submission feedback states
  const [submitting, setSubmitting] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [duplicateInfo, setDuplicateInfo] = useState<DocumentDuplicateResponse | null>(null);
  const [successInfo, setSuccessInfo] = useState<DocumentIngestionResponse | null>(null);

  // Reset form when opening or changing tabs
  useEffect(() => {
    if (isOpen) {
      setValidationError(null);
      setErrorMessage(null);
      setDuplicateInfo(null);
      setSuccessInfo(null);
    }
  }, [isOpen, activeTab]);

  // Handle escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !submitting) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, submitting, onClose]);

  if (!isOpen) return null;

  // Format file size helper
  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  // URL validation helper
  const validateUrl = (value: string): boolean => {
    const trimmed = value.trim();
    if (!trimmed) {
      setValidationError('La URL no puede estar vacía.');
      return false;
    }
    if (!trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
      setValidationError('La URL debe comenzar por http:// o https://');
      return false;
    }
    try {
      const parsed = new URL(trimmed);
      if (!parsed.hostname || !parsed.hostname.includes('.')) {
        setValidationError('La URL debe contener un dominio válido.');
        return false;
      }
    } catch {
      setValidationError('La URL especificada no tiene un formato válido.');
      return false;
    }
    setValidationError(null);
    return true;
  };

  // File validation helper
  const validateSelectedFile = (selectedFile: File): boolean => {
    const name = selectedFile.name.toLowerCase();
    const ext = '.' + name.split('.').pop();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setValidationError('Formato de archivo no soportado. Se admiten únicamente archivos PDF, TXT y MD.');
      return false;
    }
    if (selectedFile.size > MAX_FILE_SIZE_BYTES) {
      setValidationError('El archivo supera el tamaño máximo permitido de 25 MB.');
      return false;
    }
    if (selectedFile.size === 0) {
      setValidationError('El archivo seleccionado está vacío.');
      return false;
    }
    setValidationError(null);
    return true;
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) {
      if (validateSelectedFile(selected)) {
        setFile(selected);
      } else {
        setFile(null);
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (submitting) return;
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) {
      if (validateSelectedFile(dropped)) {
        setFile(dropped);
      } else {
        setFile(null);
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    }
  };

  const mapErrorMessage = (err: unknown): string => {
    if (err instanceof ApiError) {
      switch (err.status) {
        case 400:
          return err.detail || 'La URL o el recurso no es válido o ha sido rechazado por motivos de seguridad.';
        case 401:
          return 'Sesión no válida o expirada. Por favor, vuelva a iniciar sesión.';
        case 413:
          return 'El archivo supera el tamaño máximo permitido de 25 MB.';
        case 415:
          return 'Formato de archivo no soportado. Formatos admitidos: PDF, TXT, MD.';
        case 422:
          return err.detail || 'No se pudo extraer texto del documento. Verifique que contiene texto digital legible.';
        case 500:
          return 'Error interno del servidor al procesar el documento. Inténtelo de nuevo más tarde.';
        default:
          return err.detail || err.message;
      }
    }
    if (err instanceof Error) {
      return err.message;
    }
    return 'Ha ocurrido un error inesperado al procesar la solicitud.';
  };

  const handleUrlSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setDuplicateInfo(null);
    setSuccessInfo(null);

    if (!validateUrl(url)) {
      return;
    }

    setSubmitting(true);
    try {
      const res = await documentsApi.ingestDocumentFromUrl({
        url: url.trim(),
        analyze_with_ai: urlAnalyzeAi,
        manual_title: urlManualTitle.trim() || undefined,
        manual_author: urlManualAuthor.trim() || undefined,
        manual_published_at: urlManualDate ? new Date(urlManualDate).toISOString() : undefined,
      });

      if (res.status === 'duplicate') {
        setDuplicateInfo(res);
      } else {
        setSuccessInfo(res);
        if (onDocumentAdded) {
          onDocumentAdded(res.entry_id);
        }
      }
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409 && err.duplicateData) {
        setDuplicateInfo(err.duplicateData);
      } else {
        setErrorMessage(mapErrorMessage(err));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleFileSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setDuplicateInfo(null);
    setSuccessInfo(null);

    if (!file) {
      setValidationError('Debe seleccionar un archivo para continuar.');
      return;
    }

    if (!validateSelectedFile(file)) {
      return;
    }

    setSubmitting(true);
    try {
      const res = await documentsApi.uploadDocument({
        file,
        analyze_with_ai: fileAnalyzeAi,
        manual_title: fileManualTitle.trim() || undefined,
        manual_author: fileManualAuthor.trim() || undefined,
        manual_published_at: fileManualDate ? new Date(fileManualDate).toISOString() : undefined,
      });

      if (res.status === 'duplicate') {
        setDuplicateInfo(res);
      } else {
        setSuccessInfo(res);
        if (onDocumentAdded) {
          onDocumentAdded(res.entry_id);
        }
      }
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409 && err.duplicateData) {
        setDuplicateInfo(err.duplicateData);
      } else {
        setErrorMessage(mapErrorMessage(err));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-document-modal-title"
    >
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-xl flex flex-col overflow-hidden animate-fadeIn">
        {/* Modal Header */}
        <div className="px-6 py-4 bg-slate-900 text-white flex items-center justify-between border-b border-slate-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-slate-800 rounded-lg text-legal-gold">
              <FileText className="w-5 h-5" />
            </div>
            <div>
              <h2 id="add-document-modal-title" className="text-base font-bold tracking-wide">
                Añadir Documento al Fondo
              </h2>
              <p className="text-xs text-slate-400">
                Incorporación manual de resoluciones, sentencias o dictámenes
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors disabled:opacity-50"
            title="Cerrar modal"
            aria-label="Cerrar modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Mode Selector Tabs */}
        <div className="flex border-b border-slate-200 bg-slate-50 px-6 pt-3 gap-2">
          <button
            type="button"
            onClick={() => setActiveTab('url')}
            disabled={submitting}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold rounded-t-lg transition-colors border-t border-x ${
              activeTab === 'url'
                ? 'bg-white border-slate-200 text-navy-950 shadow-sm -mb-px'
                : 'border-transparent text-slate-500 hover:text-slate-800 hover:bg-slate-100'
            }`}
          >
            <Globe className="w-4 h-4 text-navy-800" />
            <span>Desde URL</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('file')}
            disabled={submitting}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold rounded-t-lg transition-colors border-t border-x ${
              activeTab === 'file'
                ? 'bg-white border-slate-200 text-navy-950 shadow-sm -mb-px'
                : 'border-transparent text-slate-500 hover:text-slate-800 hover:bg-slate-100'
            }`}
          >
            <UploadCloud className="w-4 h-4 text-navy-800" />
            <span>Desde archivo</span>
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 overflow-y-auto max-h-[75vh] space-y-5">
          {/* Success Banner */}
          {successInfo && (
            <div className="p-4 rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-900 space-y-2">
              <div className="flex items-center gap-2 font-bold text-sm">
                <CheckCircle2 className="w-5 h-5 text-emerald-600 shrink-0" />
                <span>Documento añadido correctamente</span>
              </div>
              <p className="text-xs text-emerald-800">
                {successInfo.title || 'El documento ha sido indexado en el Fondo Documental.'}
              </p>
              {successInfo.analysis_requested && (
                <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-100/70 px-2.5 py-1.5 rounded font-medium mt-1">
                  <Sparkles className="w-3.5 h-3.5 text-legal-gold" />
                  <span>El análisis IA ha quedado programado y se ejecutará en segundo plano.</span>
                </div>
              )}
              <div className="pt-2 flex justify-end gap-2">
                <Link
                  to={`/documentos/${successInfo.entry_id}`}
                  onClick={onClose}
                  className="px-3 py-1.5 bg-emerald-700 text-white rounded text-xs font-semibold hover:bg-emerald-800 transition-colors inline-flex items-center gap-1"
                >
                  <span>Ver documento</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </Link>
                <button
                  type="button"
                  onClick={onClose}
                  className="px-3 py-1.5 bg-white text-slate-700 border border-slate-300 rounded text-xs font-medium hover:bg-slate-50 transition-colors"
                >
                  Cerrar
                </button>
              </div>
            </div>
          )}

          {/* Duplicate Banner */}
          {duplicateInfo && (
            <div className="p-4 rounded-lg bg-amber-50 border border-amber-200 text-amber-900 space-y-2.5">
              <div className="flex items-center gap-2 font-bold text-sm">
                <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0" />
                <span>Este documento ya existe en el Fondo Documental</span>
              </div>
              <p className="text-xs text-amber-800">
                {duplicateInfo.message || 'Se ha detectado una resolución idéntica previamente registrada en el archivo.'}
              </p>
              <div className="pt-2 flex items-center justify-between">
                <span className="text-[11px] text-amber-700 font-mono">
                  Motivo: {duplicateInfo.duplicate_reason === 'url' ? 'URL coincidente' : 'Contenido idéntico'}
                </span>
                <Link
                  to={`/documentos/${duplicateInfo.existing_entry_id}`}
                  onClick={onClose}
                  className="px-3 py-1.5 bg-navy-900 text-white rounded text-xs font-semibold hover:bg-navy-800 transition-colors inline-flex items-center gap-1.5 shadow-sm"
                >
                  <span>Ver documento existente</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </Link>
              </div>
            </div>
          )}

          {/* Error Banner */}
          {errorMessage && (
            <div className="p-3.5 rounded-lg bg-red-50 border border-red-200 text-red-800 flex items-start gap-2.5 text-xs">
              <AlertTriangle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
              <div className="flex-1 font-medium leading-relaxed">{errorMessage}</div>
            </div>
          )}

          {/* Validation Error Banner */}
          {validationError && (
            <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 flex items-start gap-2 text-xs">
              <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
              <span>{validationError}</span>
            </div>
          )}

          {/* Forms */}
          {!successInfo && (
            <>
              {activeTab === 'url' ? (
                <form onSubmit={handleUrlSubmit} className="space-y-4">
                  <div>
                    <label htmlFor="url-input" className="block text-xs font-bold text-slate-800 mb-1">
                      URL del documento <span className="text-red-500">*</span>
                    </label>
                    <input
                      key="url-input"
                      id="url-input"
                      type="url"
                      required
                      value={url}
                      disabled={submitting}
                      onChange={(e) => {
                        setUrl(e.target.value);
                        if (validationError) validateUrl(e.target.value);
                      }}
                      placeholder="https://cnmc.es/expedientes/resolucion-123.pdf"
                      className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:ring-1 focus:ring-navy-900 focus:border-navy-900 disabled:bg-slate-100"
                    />
                    <p className="text-[11px] text-slate-500 mt-1">
                      Se admiten páginas web con texto HTML o enlaces directos a resoluciones en formato PDF.
                    </p>
                  </div>

                  {/* AI Analysis Option */}
                  <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
                    <label className="flex items-start gap-2.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={urlAnalyzeAi}
                        disabled={submitting}
                        onChange={(e) => setUrlAnalyzeAi(e.target.checked)}
                        className="mt-0.5 h-4 w-4 rounded border-slate-300 text-navy-900 focus:ring-navy-900"
                      />
                      <div>
                        <span className="text-xs font-bold text-slate-900 flex items-center gap-1.5">
                          <Sparkles className="w-3.5 h-3.5 text-legal-gold" />
                          Analizar con IA (evaluación de relevancia y dictamen)
                        </span>
                        <p className="text-[11px] text-slate-500 mt-0.5">
                          El pipeline evaluará la relevancia jurídica y clasificará los temas correspondientes en segundo plano.
                        </p>
                      </div>
                    </label>
                  </div>

                  {/* Collapsible Manual Metadata */}
                  <div className="border border-slate-200 rounded-lg overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setUrlShowAdvanced(!urlShowAdvanced)}
                      className="w-full px-3 py-2 bg-slate-50 hover:bg-slate-100 text-left text-xs font-semibold text-slate-700 flex items-center justify-between transition-colors"
                    >
                      <span>Metadatos opcionales (título, autor, fecha)</span>
                      {urlShowAdvanced ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                    </button>

                    {urlShowAdvanced && (
                      <div className="p-3 space-y-3 bg-white border-t border-slate-200">
                        <div>
                          <label htmlFor="url-manual-title" className="block text-[11px] font-medium text-slate-700 mb-0.5">
                            Título personalizado (opcional)
                          </label>
                          <input
                            id="url-manual-title"
                            type="text"
                            value={urlManualTitle}
                            disabled={submitting}
                            onChange={(e) => setUrlManualTitle(e.target.value)}
                            placeholder="Dejar en blanco para extraer automáticamente"
                            className="w-full px-2.5 py-1.5 border border-slate-300 rounded text-xs focus:ring-1 focus:ring-navy-900"
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label htmlFor="url-manual-author" className="block text-[11px] font-medium text-slate-700 mb-0.5">
                              Autor / Tribunal
                            </label>
                            <input
                              id="url-manual-author"
                              type="text"
                              value={urlManualAuthor}
                              disabled={submitting}
                              onChange={(e) => setUrlManualAuthor(e.target.value)}
                              placeholder="Ej. CNMC, TJUE, CAT"
                              className="w-full px-2.5 py-1.5 border border-slate-300 rounded text-xs focus:ring-1 focus:ring-navy-900"
                            />
                          </div>
                          <div>
                            <label htmlFor="url-manual-date" className="block text-[11px] font-medium text-slate-700 mb-0.5">
                              Fecha de publicación
                            </label>
                            <input
                              id="url-manual-date"
                              type="date"
                              value={urlManualDate}
                              disabled={submitting}
                              onChange={(e) => setUrlManualDate(e.target.value)}
                              className="w-full px-2.5 py-1.5 border border-slate-300 rounded text-xs focus:ring-1 focus:ring-navy-900"
                            />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="pt-3 border-t border-slate-100 flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={onClose}
                      disabled={submitting}
                      className="px-4 py-2 border border-slate-300 rounded-md text-xs font-semibold text-slate-700 hover:bg-slate-50 transition-colors disabled:opacity-50"
                    >
                      Cancelar
                    </button>
                    <button
                      type="submit"
                      disabled={submitting || !url.trim()}
                      className="px-4 py-2 bg-navy-900 text-white rounded-md text-xs font-semibold hover:bg-navy-800 transition-colors disabled:opacity-50 flex items-center gap-2 shadow-sm"
                    >
                      {submitting ? (
                        <>
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                          <span>Procesando...</span>
                        </>
                      ) : (
                        <span>Añadir documento</span>
                      )}
                    </button>
                  </div>
                </form>
              ) : (
                <form onSubmit={handleFileSubmit} className="space-y-4">
                  {/* File Upload Dropzone */}
                  <div>
                    <label className="block text-xs font-bold text-slate-800 mb-1">
                      Archivo documental <span className="text-red-500">*</span>
                    </label>

                    <input
                      key="file-upload-input"
                      ref={fileInputRef}
                      type="file"
                      id="file-upload-input"
                      accept=".pdf,.txt,.md"
                      disabled={submitting}
                      onChange={handleFileChange}
                      className="hidden"
                    />

                    {!file ? (
                      <div
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={handleDrop}
                        onClick={() => fileInputRef.current?.click()}
                        className="border-2 border-dashed border-slate-300 hover:border-navy-800 rounded-lg p-6 text-center cursor-pointer transition-colors bg-slate-50/50 hover:bg-slate-50"
                      >
                        <UploadCloud className="w-8 h-8 text-slate-400 mx-auto mb-2" />
                        <p className="text-xs font-bold text-slate-800">
                          Haga clic para seleccionar o arrastre un archivo aquí
                        </p>
                        <p className="text-[11px] text-slate-500 mt-1">
                          Formatos admitidos: <strong>PDF, TXT, MD</strong> (máximo 25 MB)
                        </p>
                      </div>
                    ) : (
                      <div className="p-3.5 bg-slate-50 border border-slate-200 rounded-lg flex items-center justify-between">
                        <div className="flex items-center gap-2.5 min-w-0">
                          <FileText className="w-6 h-6 text-navy-800 shrink-0" />
                          <div className="min-w-0">
                            <p className="text-xs font-bold text-slate-900 truncate">{file.name}</p>
                            <p className="text-[11px] text-slate-500">
                              {formatFileSize(file.size)} • {file.type || 'document'}
                            </p>
                          </div>
                        </div>
                        <button
                          type="button"
                          disabled={submitting}
                          onClick={() => {
                            setFile(null);
                            if (fileInputRef.current) fileInputRef.current.value = '';
                          }}
                          className="text-xs text-red-600 hover:text-red-800 font-semibold p-1 transition-colors"
                        >
                          Eliminar
                        </button>
                      </div>
                    )}
                  </div>

                  {/* AI Analysis Option */}
                  <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
                    <label className="flex items-start gap-2.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={fileAnalyzeAi}
                        disabled={submitting}
                        onChange={(e) => setFileAnalyzeAi(e.target.checked)}
                        className="mt-0.5 h-4 w-4 rounded border-slate-300 text-navy-900 focus:ring-navy-900"
                      />
                      <div>
                        <span className="text-xs font-bold text-slate-900 flex items-center gap-1.5">
                          <Sparkles className="w-3.5 h-3.5 text-legal-gold" />
                          Analizar con IA (evaluación de relevancia y dictamen)
                        </span>
                        <p className="text-[11px] text-slate-500 mt-0.5">
                          El pipeline evaluará la relevancia jurídica y clasificará los temas correspondientes en segundo plano.
                        </p>
                      </div>
                    </label>
                  </div>

                  {/* Collapsible Manual Metadata */}
                  <div className="border border-slate-200 rounded-lg overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setFileShowAdvanced(!fileShowAdvanced)}
                      className="w-full px-3 py-2 bg-slate-50 hover:bg-slate-100 text-left text-xs font-semibold text-slate-700 flex items-center justify-between transition-colors"
                    >
                      <span>Metadatos opcionales (título, autor, fecha)</span>
                      {fileShowAdvanced ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                    </button>

                    {fileShowAdvanced && (
                      <div className="p-3 space-y-3 bg-white border-t border-slate-200">
                        <div>
                          <label htmlFor="file-manual-title" className="block text-[11px] font-medium text-slate-700 mb-0.5">
                            Título personalizado (opcional)
                          </label>
                          <input
                            id="file-manual-title"
                            type="text"
                            value={fileManualTitle}
                            disabled={submitting}
                            onChange={(e) => setFileManualTitle(e.target.value)}
                            placeholder="Por defecto se utilizará el nombre del archivo"
                            className="w-full px-2.5 py-1.5 border border-slate-300 rounded text-xs focus:ring-1 focus:ring-navy-900"
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label htmlFor="file-manual-author" className="block text-[11px] font-medium text-slate-700 mb-0.5">
                              Autor / Tribunal
                            </label>
                            <input
                              id="file-manual-author"
                              type="text"
                              value={fileManualAuthor}
                              disabled={submitting}
                              onChange={(e) => setFileManualAuthor(e.target.value)}
                              placeholder="Ej. Audiencia Nacional, CNMC"
                              className="w-full px-2.5 py-1.5 border border-slate-300 rounded text-xs focus:ring-1 focus:ring-navy-900"
                            />
                          </div>
                          <div>
                            <label htmlFor="file-manual-date" className="block text-[11px] font-medium text-slate-700 mb-0.5">
                              Fecha de publicación
                            </label>
                            <input
                              id="file-manual-date"
                              type="date"
                              value={fileManualDate}
                              disabled={submitting}
                              onChange={(e) => setFileManualDate(e.target.value)}
                              className="w-full px-2.5 py-1.5 border border-slate-300 rounded text-xs focus:ring-1 focus:ring-navy-900"
                            />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="pt-3 border-t border-slate-100 flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={onClose}
                      disabled={submitting}
                      className="px-4 py-2 border border-slate-300 rounded-md text-xs font-semibold text-slate-700 hover:bg-slate-50 transition-colors disabled:opacity-50"
                    >
                      Cancelar
                    </button>
                    <button
                      type="submit"
                      disabled={submitting || !file}
                      className="px-4 py-2 bg-navy-900 text-white rounded-md text-xs font-semibold hover:bg-navy-800 transition-colors disabled:opacity-50 flex items-center gap-2 shadow-sm"
                    >
                      {submitting ? (
                        <>
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                          <span>Procesando...</span>
                        </>
                      ) : (
                        <span>Añadir documento</span>
                      )}
                    </button>
                  </div>
                </form>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
