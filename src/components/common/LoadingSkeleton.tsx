import React from 'react';

export const CardSkeleton: React.FC = () => (
  <div className="bg-white rounded-lg border border-slate-200 p-5 shadow-sm animate-pulse">
    <div className="flex items-center justify-between mb-3">
      <div className="h-4 bg-slate-200 rounded w-1/4"></div>
      <div className="h-6 bg-slate-200 rounded-full w-24"></div>
    </div>
    <div className="h-6 bg-slate-200 rounded w-3/4 mb-3"></div>
    <div className="space-y-2 mb-4">
      <div className="h-4 bg-slate-100 rounded w-full"></div>
      <div className="h-4 bg-slate-100 rounded w-5/6"></div>
    </div>
    <div className="flex gap-2 pt-2 border-t border-slate-100">
      <div className="h-5 bg-slate-200 rounded w-20"></div>
      <div className="h-5 bg-slate-200 rounded w-28"></div>
    </div>
  </div>
);

export const KPISkeleton: React.FC = () => (
  <div className="bg-white rounded-lg border border-slate-200 p-5 shadow-sm animate-pulse">
    <div className="h-4 bg-slate-200 rounded w-1/2 mb-3"></div>
    <div className="h-8 bg-slate-200 rounded w-1/3 mb-2"></div>
    <div className="h-3 bg-slate-100 rounded w-2/3"></div>
  </div>
);

export const DetailSkeleton: React.FC = () => (
  <div className="bg-white rounded-lg border border-slate-200 p-8 shadow-sm animate-pulse space-y-6">
    <div className="flex justify-between items-center">
      <div className="h-4 bg-slate-200 rounded w-1/3"></div>
      <div className="h-7 bg-slate-200 rounded-full w-28"></div>
    </div>
    <div className="h-8 bg-slate-200 rounded w-4/5"></div>
    <div className="h-5 bg-slate-100 rounded w-1/4"></div>
    <div className="space-y-3 pt-4 border-t border-slate-100">
      <div className="h-4 bg-slate-200 rounded w-full"></div>
      <div className="h-4 bg-slate-200 rounded w-full"></div>
      <div className="h-4 bg-slate-200 rounded w-3/4"></div>
    </div>
  </div>
);
