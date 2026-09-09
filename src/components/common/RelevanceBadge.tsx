import React from 'react';
import { RelevanceStatus } from '../../types/observatory';
import { CheckCircle2, AlertCircle, MinusCircle } from 'lucide-react';

interface RelevanceBadgeProps {
  status: RelevanceStatus;
  score?: number;
  showScore?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

export const RelevanceBadge: React.FC<RelevanceBadgeProps> = ({
  status,
  score,
  showScore = true,
  size = 'md',
}) => {
  let label = 'Relevante';
  let badgeClasses = 'bg-emerald-50 text-emerald-800 border-emerald-200/80';
  let Icon = CheckCircle2;

  if (status === 'uncertain') {
    label = 'En revisión';
    badgeClasses = 'bg-amber-50 text-amber-800 border-amber-200/80';
    Icon = AlertCircle;
  } else if (status === 'not_relevant') {
    label = 'No relevante';
    badgeClasses = 'bg-slate-100 text-slate-700 border-slate-200';
    Icon = MinusCircle;
  }

  const sizeClasses = {
    sm: 'text-xs px-2 py-0.5 gap-1',
    md: 'text-xs px-2.5 py-1 gap-1.5 font-medium',
    lg: 'text-sm px-3.5 py-1.5 gap-2 font-semibold',
  }[size];

  return (
    <span
      className={`inline-flex items-center rounded-full border shadow-sm transition-colors ${badgeClasses} ${sizeClasses}`}
      title={`Estado analítico: ${label}${score !== undefined ? ` (Puntuación: ${score}/100)` : ''}`}
    >
      <Icon className={size === 'sm' ? 'w-3 h-3' : size === 'lg' ? 'w-4 h-4' : 'w-3.5 h-3.5'} />
      <span>{label}</span>
      {showScore && score !== undefined && (
        <span className="font-mono text-[11px] font-semibold opacity-85 ml-0.5">
          {score}
        </span>
      )}
    </span>
  );
};
