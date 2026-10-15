import React, { useState, useEffect, useCallback } from 'react';
import {
  Tag,
  Search,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Sliders,
  ArrowLeft,
  XCircle,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { TrackingTopic } from '../types/tracking';
import { trackingApi } from '../services/trackingApi';
import { TopicSelector } from '../components/keywords/TopicSelector';
import { KeywordList } from '../components/keywords/KeywordList';

export const KeywordSettingsPage: React.FC = () => {
  const [topics, setTopics] = useState<TrackingTopic[]>([]);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const loadTopics = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await trackingApi.getTopics();
      // Sort topics by priority desc, name asc
      const sorted = [...data].sort((a, b) => b.priority - a.priority || a.name.localeCompare(b.name));
      setTopics(sorted);
      if (sorted.length > 0 && !selectedTopicId) {
        setSelectedTopicId(sorted[0].id);
      }
    } catch (err: any) {
      console.error('Error loading tracking topics:', err);
      setError('No se han podido cargar los temas y palabras clave configurados.');
    } finally {
      setLoading(false);
    }
  }, [selectedTopicId]);

  useEffect(() => {
    loadTopics();
  }, [loadTopics]);

  // Clear feedback banner after 4 seconds
  useEffect(() => {
    if (feedback) {
      const timer = setTimeout(() => {
        setFeedback(null);
      }, 4000);
      return () => clearTimeout(timer);
    }
  }, [feedback]);

  const selectedTopic = topics.find((t) => t.id === selectedTopicId) || topics[0] || null;

  const handleAddKeyword = async (newKeyword: string): Promise<boolean> => {
    if (!selectedTopic) return false;
    setIsSaving(true);
    setFeedback(null);

    const currentKeywords = selectedTopic.keywords ? [...selectedTopic.keywords] : [];
    const updatedKeywords = [...currentKeywords, newKeyword];

    try {
      const updated = await trackingApi.updateTopicKeywords(selectedTopic.id, updatedKeywords);
      // Update local state cleanly
      setTopics((prev) =>
        prev.map((t) => (t.id === updated.id ? { ...t, keywords: updated.keywords || updatedKeywords } : t))
      );
      setFeedback({
        type: 'success',
        message: 'Palabra clave añadida correctamente.',
      });
      return true;
    } catch (err: any) {
      console.error('Error adding keyword:', err);
      setFeedback({
        type: 'error',
        message: 'No se ha podido guardar el cambio.',
      });
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  const handleUpdateKeyword = async (
    oldKeyword: string,
    newKeyword: string,
    index: number
  ): Promise<boolean> => {
    if (!selectedTopic) return false;
    setIsSaving(true);
    setFeedback(null);

    const currentKeywords = selectedTopic.keywords ? [...selectedTopic.keywords] : [];
    if (index >= 0 && index < currentKeywords.length) {
      currentKeywords[index] = newKeyword;
    } else {
      const fallbackIdx = currentKeywords.indexOf(oldKeyword);
      if (fallbackIdx !== -1) {
        currentKeywords[fallbackIdx] = newKeyword;
      } else {
        currentKeywords.push(newKeyword);
      }
    }

    try {
      const updated = await trackingApi.updateTopicKeywords(selectedTopic.id, currentKeywords);
      setTopics((prev) =>
        prev.map((t) => (t.id === updated.id ? { ...t, keywords: updated.keywords || currentKeywords } : t))
      );
      setFeedback({
        type: 'success',
        message: 'Palabra clave actualizada correctamente.',
      });
      return true;
    } catch (err: any) {
      console.error('Error updating keyword:', err);
      setFeedback({
        type: 'error',
        message: 'No se ha podido guardar el cambio.',
      });
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <Link
                to="/"
                className="text-xs text-navy-700 hover:text-navy-950 font-semibold inline-flex items-center gap-1 transition-colors"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
                <span>Volver al Cuadro de Mando</span>
              </Link>
              <span className="text-slate-300">•</span>
              <span className="text-xs text-legal-gold font-semibold uppercase tracking-wider flex items-center gap-1">
                <Sliders className="w-3.5 h-3.5" />
                Configuración del Observatorio
              </span>
            </div>

            <h1 className="text-2xl font-bold font-serif text-slate-900 tracking-tight flex items-center gap-2.5">
              <Tag className="w-6 h-6 text-legal-gold" />
              <span>Gestión de Palabras Clave por Tema</span>
            </h1>

            <p className="text-xs sm:text-sm text-slate-600 mt-1.5 max-w-3xl leading-relaxed">
              Consulta, añade y ajusta los términos y señales de seguimiento utilizados en el descubrimiento y análisis
              del Observatorio. Los cambios se aplican de forma inmediata sobre la matriz activa.
            </p>
          </div>

          <div className="flex items-center gap-2 self-start sm:self-center">
            <button
              type="button"
              onClick={loadTopics}
              disabled={loading}
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg border border-slate-300 transition-colors"
              title="Recargar datos"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              <span>Actualizar</span>
            </button>
          </div>
        </div>

        {/* Global Search Bar */}
        <div className="mt-5 pt-4 border-t border-slate-100">
          <div className="relative max-w-lg">
            <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-3" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Buscar tema o palabra clave (ej: DMA, 101 TFUE, cártel, IA)..."
              className="w-full pl-10 pr-4 py-2 text-sm rounded-lg border border-slate-200 bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-navy-800 focus:border-navy-800 shadow-xs transition-colors"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-3 top-2.5 text-xs text-slate-400 hover:text-slate-600 cursor-pointer"
                aria-label="Limpiar búsqueda"
              >
                ✕
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Feedback Banner */}
      {feedback && (
        <div
          role="alert"
          className={`p-4 rounded-lg flex items-center justify-between shadow-xs transition-all ${
            feedback.type === 'success'
              ? 'bg-emerald-50 text-emerald-900 border border-emerald-200'
              : 'bg-red-50 text-red-900 border border-red-200'
          }`}
        >
          <div className="flex items-center gap-2.5 text-sm font-medium">
            {feedback.type === 'success' ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-600 flex-shrink-0" />
            ) : (
              <XCircle className="w-5 h-5 text-red-600 flex-shrink-0" />
            )}
            <span>{feedback.message}</span>
          </div>

          <button
            type="button"
            onClick={() => setFeedback(null)}
            className="p-1 rounded text-slate-400 hover:text-slate-600 cursor-pointer"
            aria-label="Cerrar notificación"
          >
            ✕
          </button>
        </div>
      )}

      {/* Main Content Area */}
      {loading && topics.length === 0 ? (
        <div className="bg-white rounded-lg border border-slate-200 p-12 text-center shadow-sm">
          <RefreshCw className="w-8 h-8 text-navy-700 animate-spin mx-auto mb-3" />
          <p className="text-sm font-semibold text-slate-700">Cargando temas y palabras clave...</p>
        </div>
      ) : error ? (
        <div className="bg-white rounded-lg border border-red-200 p-8 text-center shadow-sm">
          <AlertTriangle className="w-8 h-8 text-red-500 mx-auto mb-2" />
          <p className="text-sm font-semibold text-red-800">{error}</p>
          <button
            type="button"
            onClick={loadTopics}
            className="mt-4 px-4 py-2 bg-navy-900 hover:bg-navy-950 text-white text-xs font-semibold rounded-md transition-colors"
          >
            Reintentar carga
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Left Column: Topics list (4 cols) */}
          <div className="lg:col-span-4 h-full">
            <TopicSelector
              topics={topics}
              selectedTopicId={selectedTopic?.id || null}
              onSelectTopic={(id) => setSelectedTopicId(id)}
              searchQuery={searchQuery}
            />
          </div>

          {/* Right Column: Keywords of selected topic (8 cols) */}
          <div className="lg:col-span-8 h-full">
            {selectedTopic ? (
              <KeywordList
                topic={selectedTopic}
                keywords={selectedTopic.keywords || []}
                searchQuery={searchQuery}
                onAddKeyword={handleAddKeyword}
                onUpdateKeyword={handleUpdateKeyword}
                isSaving={isSaving}
              />
            ) : (
              <div className="bg-white rounded-lg border border-slate-200 p-12 text-center text-slate-400 shadow-sm">
                Selecciona un tema para ver sus palabras clave.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
