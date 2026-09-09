import React, { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Calendar,
  ExternalLink,
  Quote,
  CheckCircle2,
  Bookmark,
  FileCheck,
  Scale,
  Sparkles,
  AlertTriangle,
} from 'lucide-react';
import { observatoryApi } from '../services/observatoryApi';
import { ObservatoryEntryDetail } from '../types/observatory';
import { RelevanceBadge } from '../components/common/RelevanceBadge';
import { TopicBadge } from '../components/common/TopicBadge';
import { DetailSkeleton } from '../components/common/LoadingSkeleton';

export const EntryDetailPage: React.FC = () => {
  const { entryId } = useParams<{ entryId: string }>();
  const navigate = useNavigate();

  const [entry, setEntry] = useState<ObservatoryEntryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadDetail() {
      if (!entryId) return;
      setLoading(true);
      setError(null);
      try {
        const data = await observatoryApi.getEntry(entryId);
        setEntry(data);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : 'No se pudo cargar la publicación');
      } finally {
        setLoading(false);
      }
    }
    loadDetail();
  }, [entryId]);

  if (loading) {
    return <DetailSkeleton />;
  }

  if (error || !entry) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 p-10 text-center max-w-xl mx-auto my-12 shadow-sm">
        <div className="w-12 h-12 bg-amber-50 text-amber-600 rounded-full flex items-center justify-center mx-auto mb-4 border border-amber-200">
          <AlertTriangle className="w-6 h-6" />
        </div>
        <h2 className="text-lg font-bold text-slate-900 mb-2">Publicación no encontrada</h2>
        <p className="text-sm text-slate-600 mb-6">
          {error || 'La entrada solicitada no existe en el fondo documental o no dispone de un análisis jurídico vigente.'}
        </p>
        <Link
          to="/observatorio"
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy-900 text-white rounded-md text-sm font-medium hover:bg-navy-800 transition-colors shadow-sm"
        >
          <ArrowLeft className="w-4 h-4" />
          Volver al Observatorio
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8 max-w-5xl mx-auto">
      {/* Top Navigation & Breadcrumb */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-slate-500">
        <div className="flex items-center gap-2">
          <Link to="/" className="hover:text-slate-800">
            Inicio
          </Link>
          <span>/</span>
          <Link to="/observatorio" className="hover:text-slate-800">
            Observatorio
          </Link>
          <span>/</span>
          <span className="text-slate-700 font-medium truncate max-w-xs">
            {entry.title}
          </span>
        </div>

        <button
          type="button"
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-1.5 text-navy-800 hover:text-navy-950 font-semibold"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Volver atrás</span>
        </button>
      </div>

      {/* Main Entry Header Card */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 sm:p-8 shadow-sm">
        {/* Source metadata & relevance badge */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-semibold text-navy-800 bg-navy-50 px-2.5 py-1 rounded border border-navy-100 flex items-center gap-1.5">
              <Scale className="w-3.5 h-3.5 text-navy-600" />
              {entry.source.name}
            </span>
            {entry.content_type && (
              <span className="text-xs uppercase tracking-wider text-slate-500 bg-slate-100 px-2.5 py-1 rounded font-mono font-medium">
                {entry.content_type}
              </span>
            )}
            <span className="text-xs text-slate-400 flex items-center gap-1">
              <Calendar className="w-3.5 h-3.5" />
              {new Date(entry.published_at).toLocaleDateString('es-ES', {
                day: 'numeric',
                month: 'long',
                year: 'numeric',
              })}
            </span>
          </div>

          <RelevanceBadge
            status={entry.relevance.status}
            score={entry.relevance.score}
            size="lg"
          />
        </div>

        {/* Stately Title */}
        <h1 className="text-xl sm:text-2xl lg:text-3xl font-bold font-serif text-slate-950 leading-tight mb-4">
          {entry.title}
        </h1>

        {/* Canonical Topics Tags */}
        <div className="flex flex-wrap items-center gap-2 mb-6">
          {entry.canonical_primary_topic && (
            <TopicBadge
              name={entry.canonical_primary_topic.name}
              isPrimary
              onClick={() => navigate(`/observatorio?topic_code=${entry.canonical_primary_topic!.code}`)}
            />
          )}
          {entry.canonical_topics
            .filter((t) => t.code !== entry.canonical_primary_topic?.code)
            .map((t) => (
              <TopicBadge
                key={t.code}
                name={t.name}
                onClick={() => navigate(`/observatorio?topic_code=${t.code}`)}
              />
            ))}
        </div>

        {/* Action button: Official Source */}
        {entry.url && (
          <div className="pt-4 border-t border-slate-100 flex items-center justify-between">
            <span className="text-xs text-slate-400">
              Documento original publicado en el portal oficial del organismo.
            </span>
            <a
              href={entry.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-4 py-2 bg-navy-950 hover:bg-navy-800 text-white rounded-md text-xs font-semibold shadow-sm transition-colors"
            >
              <span>Acceder al documento oficial</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        )}
      </div>

      {/* Section 1: Executive Summary */}
      <section className="bg-white rounded-xl border border-slate-200 p-6 sm:p-8 shadow-sm">
        <div className="flex items-center gap-2 pb-3 mb-4 border-b border-slate-100">
          <Bookmark className="w-4 h-4 text-legal-gold" />
          <h2 className="text-sm font-bold uppercase tracking-wider text-slate-900">
            Resumen Ejecutivo Jurídico
          </h2>
        </div>
        <p className="text-base text-slate-800 leading-relaxed font-sans">
          {entry.summary}
        </p>
      </section>

      {/* Section 2: Key Points */}
      {entry.key_points && entry.key_points.length > 0 && (
        <section className="bg-white rounded-xl border border-slate-200 p-6 sm:p-8 shadow-sm">
          <div className="flex items-center gap-2 pb-3 mb-5 border-b border-slate-100">
            <Sparkles className="w-4 h-4 text-legal-gold" />
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-900">
              Puntos Clave del Pronunciamiento
            </h2>
          </div>

          <div className="space-y-3">
            {entry.key_points.map((point, idx) => (
              <div
                key={idx}
                className="flex items-start gap-3.5 p-4 rounded-lg bg-slate-50 border border-slate-100"
              >
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-navy-900 text-white text-xs font-bold flex items-center justify-center font-mono">
                  {idx + 1}
                </span>
                <p className="text-sm text-slate-800 font-medium leading-relaxed pt-0.5">
                  {point}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Section 3: Grounded Textual Evidence Quotes */}
      {entry.evidence && (
        <section className="bg-white rounded-xl border border-slate-200 p-6 sm:p-8 shadow-sm space-y-6">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100">
            <div className="flex items-center gap-2">
              <FileCheck className="w-4 h-4 text-emerald-600" />
              <h2 className="text-sm font-bold uppercase tracking-wider text-slate-900">
                Evidencias Textuales Verificadas
              </h2>
            </div>
            <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-full border border-emerald-200">
              <CheckCircle2 className="w-3 h-3" />
              Citas validadas con el texto oficial
            </span>
          </div>

          <p className="text-xs text-slate-500 leading-relaxed">
            Extractos literales seleccionados del cuerpo de la resolución judicial o administrativa que respaldan
            el análisis jurídico y los criterios de relevancia establecidos.
          </p>

          {/* Summary Quotes */}
          {entry.evidence.summary_quotes && entry.evidence.summary_quotes.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                Citas de respaldo del Resumen Ejecutivo
              </h3>
              {entry.evidence.summary_quotes.map((q, idx) => (
                <blockquote
                  key={idx}
                  className="relative p-4 rounded-lg bg-slate-50 border-l-4 border-navy-800 text-slate-700 italic text-sm leading-relaxed"
                >
                  <Quote className="w-5 h-5 text-slate-300 absolute top-3 right-3 pointer-events-none" />
                  <p className="pr-6">"{q.quote}"</p>
                </blockquote>
              ))}
            </div>
          )}

          {/* Key Point Quotes */}
          {entry.evidence.key_points && entry.evidence.key_points.length > 0 && (
            <div className="space-y-4 pt-4 border-t border-slate-100">
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                Citas de respaldo por Punto Clave
              </h3>

              <div className="space-y-4">
                {entry.evidence.key_points.map((kp, idx) => (
                  <div key={idx} className="bg-slate-50/70 rounded-lg p-4 border border-slate-100 space-y-2">
                    <div className="text-xs font-semibold text-slate-900 flex items-center gap-2">
                      <span className="w-4 h-4 rounded bg-navy-200 text-navy-900 text-[10px] font-mono flex items-center justify-center font-bold">
                        {idx + 1}
                      </span>
                      <span>{kp.point}</span>
                    </div>

                    {kp.quotes && kp.quotes.length > 0 ? (
                      kp.quotes.map((q, qIdx) => (
                        <blockquote
                          key={qIdx}
                          className="relative p-3 rounded bg-white border-l-2 border-legal-gold text-slate-700 italic text-xs leading-relaxed shadow-2xs"
                        >
                          "{q.quote}"
                        </blockquote>
                      ))
                    ) : (
                      <p className="text-[11px] text-slate-400 italic">Sin cita textual asociada.</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
};
