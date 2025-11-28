import React from 'react';
import { ReportData } from '../types';

interface AnalysisHeaderProps {
  data: ReportData;
}

const AnalysisHeader: React.FC<AnalysisHeaderProps> = ({ data }) => {
  return (
    <div className="bg-gradient-to-r from-indigo-900 to-slate-900 rounded-2xl p-6 md:p-8 mb-8 shadow-2xl border border-indigo-500/30 relative overflow-hidden flex-grow">
      {/* Decorative Background Elements */}
      <div className="absolute top-0 right-0 -mt-10 -mr-10 w-40 h-40 bg-purple-500 rounded-full blur-[100px] opacity-20"></div>
      <div className="absolute bottom-0 left-0 -mb-10 -ml-10 w-40 h-40 bg-blue-500 rounded-full blur-[100px] opacity-20"></div>

      <div className="relative z-10">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center mb-6">
          <div>
            <h1 className="text-3xl md:text-4xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-blue-200 to-indigo-100">
              每日新游监测报告
            </h1>
            <p className="text-slate-400 mt-1 font-mono text-sm">
              生成日期: {data.date}
            </p>
          </div>
          <div className="mt-4 md:mt-0 px-4 py-2 bg-slate-800/50 rounded-full border border-slate-600/50">
            <span className="text-sm font-semibold text-blue-300">
              数据源: YouTube & Play Store
            </span>
          </div>
        </div>

        <div className="bg-slate-900/60 backdrop-blur-sm rounded-xl p-6 border-l-4 border-indigo-500">
          <h2 className="text-sm font-bold text-indigo-400 uppercase tracking-widest mb-2">市场趋势总结</h2>
          <p className="text-slate-200 leading-relaxed text-lg">
            {data.summary}
          </p>
        </div>
      </div>
    </div>
  );
};

export default AnalysisHeader;
