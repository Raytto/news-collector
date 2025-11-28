import React from 'react';
import { GroundingSource } from '../types';

interface SourceListProps {
  sources: GroundingSource[];
}

const SourceList: React.FC<SourceListProps> = ({ sources }) => {
  if (!sources || sources.length === 0) return null;

  return (
    <div className="mt-12 pt-8 border-t border-slate-700">
      <h3 className="text-sm font-bold text-slate-500 uppercase tracking-widest mb-4">
        情报来源 (AI Grounding)
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {sources.map((source, idx) => (
          <a
            key={idx}
            href={source.uri}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center p-3 rounded-lg bg-slate-800/50 hover:bg-slate-800 border border-slate-700/50 hover:border-slate-600 transition-colors group"
          >
            <div className="flex-shrink-0 mr-3 text-slate-500 group-hover:text-blue-400">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
            </div>
            <span className="text-xs text-slate-400 group-hover:text-slate-200 truncate font-mono">
              {source.title}
            </span>
          </a>
        ))}
      </div>
    </div>
  );
};

export default SourceList;
