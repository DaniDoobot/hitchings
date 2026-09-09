import React from 'react';
import { Tag } from 'lucide-react';

interface TopicBadgeProps {
  name: string;
  code?: string;
  onClick?: () => void;
  isPrimary?: boolean;
}

export const TopicBadge: React.FC<TopicBadgeProps> = ({
  name,
  onClick,
  isPrimary = false,
}) => {
  const baseClasses = isPrimary
    ? 'bg-navy-900 text-white border-navy-900 hover:bg-navy-800'
    : 'bg-slate-100 text-slate-800 border-slate-200 hover:bg-slate-200/70 hover:border-slate-300';

  const interactive = onClick ? 'cursor-pointer select-none transition-all duration-150' : '';

  return (
    <span
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-xs font-medium border ${baseClasses} ${interactive}`}
      role={onClick ? 'button' : undefined}
    >
      <Tag className={`w-3 h-3 ${isPrimary ? 'text-legal-gold' : 'text-slate-400'}`} />
      <span>{name}</span>
    </span>
  );
};
