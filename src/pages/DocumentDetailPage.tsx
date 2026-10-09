import React, { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Calendar,
  ExternalLink,
  Quote,
  CheckCircle2,
  Clock,
  Sparkles,
  AlertTriangle,
  User,
  Building2,
  FileText,
  FolderArchive,
  Info,
} from 'lucide-react';
import { documentsApi } from '../services/documentsApi';
import { DocumentDetail } from '../types/document';
import { RelevanceBadge } from '../components/common/RelevanceBadge';
import { TopicBadge } from '../components/common/TopicBadge';
import { DetailSkeleton } from '../components/common/LoadingSkeleton';

export const DocumentDetailPage: React.FC = () => {
  const { documentId } = useParams<{ documentId: string }>();
  const navigate = useNavigate();

  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadDoc() {
      if (!documentId) return;
      setLoading(true);
      setError(null);
      try {
        const data = await documentsApi.getDocument(documentId);
        setDoc(data);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : 'No se pudo cargar el documento');
      } finally {
        setLoading(false);
      }
    }
    loadDoc();
  }, [documentId]);

  if (loading) {
    return <DetailSkeleton />;
  }

  if (error || !doc) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 p-10 text-center max-w-xl mx-auto my-12 shadow-sm">
        <div className="w-12 h-12 bg-amber-50 text-amber-600 rounded-full flex items-center justify-center mx-auto mb-4 border border-amber-200">
          <AlertTriangle className="w-6 h-6" />
        </div>
        <h2 className="text-lg font-bold text-slate-900 mb-2">Documento no encontrado</h2>
        <p className="text-sm text-slate-600 mb-6">
          {error || 'El documento solicitado no existe en el fondo documental.'}
        </p>
        <Link
          to="/documentos"
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy-900 text-white rounded-md text-sm font-medium hover:bg-navy-800 transition-colors shadow-sm"
        >
          <ArrowLeft className="w-4 h-4" />
          Volver al Fondo Documental
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8 max-w-5xl mx-auto">
      {/* Breadcrumb */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-slate-500">
        <div className="flex items-center gap-2">
          <Link to="/" className="hover:text-slate-800">
            Inicio
          </Link>
          <span>/</span>
          <Link to="/documentos" className="hover:text-slate-800">
            Fondo Documental
          </Link>
          <span>/</span>
          <span className="text-slate-700 font-medium">Expediente documental</span>
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

      {/* Main Document Header Card */}
      <div className="bg-white rounded-lg border border-slate-200 p-6 md:p-8 shadow-sm">
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded bg-slate-100 text-slate-800 border border-slate-200">
            {doc.is_linkedin ? <User className="w-3.5 h-3.5" /> : <Building2 className="w-3.5 h-3.5" />}
            <span>{doc.source.name}</span>
          </span>

          {doc.has_analysis ? (
            <span className="inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded bg-emerald-50 text-emerald-800 border border-emerald-200">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
              Análisis IA Vigente
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded bg-slate-100 text-slate-600 border border-slate-200">
              <Clock className="w-3.5 h-3.5 text-slate-400" />
              Documento en Archivo Original (Sin análisis IA)
            </span>
          )}

          {doc.source.tracked_entity_name && (
            <span className="text-xs text-slate-500 font-medium ml-2">
              Entidad: <strong>{doc.source.tracked_entity_name}</strong>
            </span>
          )}
        </div>

        <h1 className="text-2xl sm:text-3xl font-serif font-bold text-slate-900 leading-tight mb-4">
          {doc.title || 'Expediente Documental'}
        </h1>

        <div className="flex flex-wrap items-center gap-y-2 gap-x-6 text-xs text-slate-600 pt-4 border-t border-slate-100">
          {doc.author && (
            <div>
              <span className="text-slate-400">Autor/Emisor:</span>{' '}
              <strong className="text-slate-800">{doc.author}</strong>
            </div>
          )}
          {doc.published_at && (
            <div className="flex items-center gap-1">
              <Calendar className="w-3.5 h-3.5 text-slate-400" />
              <span>Publicado:</span>
              <strong className="text-slate-800">
                {new Date(doc.published_at).toLocaleDateString('es-ES', {
                  year: 'numeric',
                  month: 'long',
                  day: 'numeric',
                })}
              </strong>
            </div>
          )}
          <div>
            <span className="text-slate-400">Capturado:</span>{' '}
            <strong className="text-slate-800">
              {new Date(doc.captured_at).toLocaleDateString('es-ES', {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
              })}
            </strong>
          </div>

          <a
            href={doc.canonical_url || doc.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-navy-800 hover:text-navy-950 font-semibold underline underline-offset-2 ml-auto"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            <span>Ver fuente original</span>
          </a>
        </div>
      </div>

      {/* Grid: Left Column Original Text, Right Column AI Intelligence */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left 2 Cols: Original Document Content */}
        <div className="lg:col-span-2 space-y-6">
          <div className="bg-white rounded-lg border border-slate-200 p-6 md:p-8 shadow-sm">
            <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
              <div className="flex items-center gap-2">
                <FileText className="w-5 h-5 text-navy-900" />
                <h2 className="text-lg font-serif font-bold text-slate-900">
                  Cuerpo Documental Original
                </h2>
              </div>
              <span className="text-xs text-slate-400">
                {doc.language ? `Idioma: ${doc.language.toUpperCase()}` : 'Texto completo'}
              </span>
            </div>

            {doc.content ? (
              <div className="prose prose-slate max-w-none text-sm leading-relaxed whitespace-pre-wrap font-sans text-slate-800">
                {doc.content}
              </div>
            ) : doc.excerpt ? (
              <div className="space-y-4">
                <div className="p-4 bg-slate-50 border border-slate-200 rounded text-sm text-slate-700 italic">
                  "{doc.excerpt}"
                </div>
                <p className="text-xs text-slate-500">
                  Nota: El documento original fue indexado como resumen o extracto breve. Puede consultar la publicación íntegra mediante el enlace externo.
                </p>
              </div>
            ) : (
              <p className="text-sm text-slate-500 italic">
                El texto documental íntegro se encuentra disponible directamente en el enlace oficial de origen.
              </p>
            )}
          </div>
        </div>

        {/* Right Col: AI Intelligence Summary & Evidence */}
        <div className="space-y-6">
          <div className="bg-white rounded-lg border border-slate-200 p-6 shadow-sm">
            <div className="flex items-center gap-2 mb-4 pb-3 border-b border-slate-100">
              <Sparkles className="w-4 h-4 text-legal-gold" />
              <h2 className="text-base font-serif font-bold text-slate-900">
                Dictamen Jurídico IA
              </h2>
            </div>

            {doc.has_analysis && doc.analysis ? (
              <div className="space-y-5">
                <div>
                  <span className="text-xs font-semibold text-slate-500 block mb-1">
                    Calificación de Relevancia
                  </span>
                  <div className="flex items-center gap-2">
                    {doc.analysis.relevance_status && (
                      <RelevanceBadge
                        status={doc.analysis.relevance_status}
                        score={doc.analysis.relevance_score ?? 0}
                      />
                    )}
                  </div>
                </div>

                {doc.analysis.canonical_topics.length > 0 && (
                  <div>
                    <span className="text-xs font-semibold text-slate-500 block mb-1.5">
                      Áreas Temáticas Asignadas
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {doc.analysis.canonical_topics.map((t) => (
                        <TopicBadge key={t.code} code={t.code} name={t.name} />
                      ))}
                    </div>
                  </div>
                )}

                {doc.analysis.summary && (
                  <div>
                    <span className="text-xs font-semibold text-slate-500 block mb-1">
                      Síntesis Analítica
                    </span>
                    <p className="text-xs text-slate-700 leading-relaxed bg-slate-50 p-3 rounded border border-slate-200">
                      {doc.analysis.summary}
                    </p>
                  </div>
                )}

                {doc.analysis.key_points.length > 0 && (
                  <div>
                    <span className="text-xs font-semibold text-slate-500 block mb-1.5">
                      Puntos Clave Identificados
                    </span>
                    <ul className="space-y-1.5 text-xs text-slate-700 list-disc list-inside">
                      {doc.analysis.key_points.map((pt, i) => (
                        <li key={i}>{pt}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Evidence quotes */}
                {doc.evidence?.summary_quotes && doc.evidence.summary_quotes.length > 0 && (
                  <div>
                    <span className="text-xs font-semibold text-slate-500 block mb-1.5">
                      Citas Verbatim de Respaldo
                    </span>
                    <div className="space-y-2">
                      {doc.evidence.summary_quotes.map((q, idx) => (
                        <div
                          key={idx}
                          className="p-2.5 bg-amber-50/50 border-l-2 border-legal-gold rounded text-[11px] text-slate-800 italic"
                        >
                          <Quote className="w-3 h-3 text-legal-gold inline mr-1" />
                          "{q.quote}"
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="p-4 bg-slate-50 rounded-lg border border-slate-200 text-center space-y-2">
                <Info className="w-6 h-6 text-slate-400 mx-auto" />
                <p className="text-xs font-semibold text-slate-700">
                  Sin análisis jurídico disponible
                </p>
                <p className="text-[11px] text-slate-500">
                  Este expediente forma parte del archivo documental original y no dispone de análisis automatizado vigente en la matriz de seguimiento.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
