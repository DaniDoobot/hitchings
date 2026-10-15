import React from 'react';
import { Layers, ChevronRight, Hash } from 'lucide-react';
import { TrackingTopic } from '../../types/tracking';

interface TopicSelectorProps {
  topics: TrackingTopic[];
  selectedTopicId: string | null;
  onSelectTopic: (topicId: string) => void;
  searchQuery?: string;
}

export const TopicSelector: React.FC<TopicSelectorProps> = ({
  topics,
  selectedTopicId,
  onSelectTopic,
  searchQuery = '',
}) => {
  const trimmed = searchQuery.trim().toLowerCase();

  const filteredTopics = topics.filter((t) => {
    if (!trimmed) return true;
    const matchesName = t.name.toLowerCase().includes(trimmed);
    const matchesCode = t.code.toLowerCase().includes(trimmed);
    const matchesKeywords = (t.keywords || []).some((kw) => kw.toLowerCase().includes(trimmed));
    return matchesName || matchesCode || matchesKeywords;
  });

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm flex flex-col h-full">
      <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center justify-between">
        <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wide flex items-center gap-2">
          <Layers className="w-4 h-4 text-navy-700" />
          <span>Temas y Materias ({filteredTopics.length})</span>
        </h3>
      </div>

      <div className="overflow-y-auto max-h-[600px] divide-y divide-slate-100 flex-1">
        {filteredTopics.length === 0 ? (
          <div className="p-6 text-center text-xs text-slate-400">
            No se han encontrado temas con el criterio de búsqueda.
          </div>
        ) : (
          filteredTopics.map((topic) => {
            const isSelected = topic.id === selectedTopicId;
            const kwCount = (topic.keywords || []).length;

            return (
              <button
                key={topic.id}
                type="button"
                onClick={() => onSelectTopic(topic.id)}
                className={`w-full text-left p-3.5 transition-all flex items-center justify-between gap-3 cursor-pointer ${
                  isSelected
                    ? 'bg-navy-50/80 border-l-4 border-l-navy-900 text-navy-950 font-medium'
                    : 'hover:bg-slate-50 text-slate-700 border-l-4 border-l-transparent'
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="text-[10px] font-mono text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                      {topic.code}
                    </span>
                    {!topic.active && (
                      <span className="text-[9px] font-semibold text-slate-400 uppercase">
                        (Inactivo)
                      </span>
                    )}
                  </div>
                  <h4 className="text-sm font-semibold text-slate-900 leading-snug truncate">
                    {topic.name}
                  </h4>
                  <div className="flex items-center gap-2 mt-1 text-[11px] text-slate-500">
                    <span className="flex items-center gap-1">
                      <Hash className="w-3 h-3 text-slate-400" />
                      <span>{kwCount} {kwCount === 1 ? 'palabra clave' : 'palabras clave'}</span>
                    </span>
                  </div>
                </div>

                <ChevronRight
                  className={`w-4 h-4 flex-shrink-0 transition-transform ${
                    isSelected ? 'text-navy-900 translate-x-0.5' : 'text-slate-300'
                  }`}
                />
              </button>
            );
          })
        )}
      </div>
    </div>
  );
};
