import React from 'react';
import { GameFinding } from '../types';

interface GameCardProps {
  game: GameFinding;
}

const GameCard: React.FC<GameCardProps> = ({ game }) => {
  const getScoreColor = (score: number) => {
    if (score >= 8) return 'text-green-400 border-green-400';
    if (score >= 5) return 'text-yellow-400 border-yellow-400';
    return 'text-red-400 border-red-400';
  };

  const getScoreBg = (score: number) => {
    if (score >= 8) return 'bg-green-500/10';
    if (score >= 5) return 'bg-yellow-500/10';
    return 'bg-red-500/10';
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-6 shadow-lg hover:shadow-slate-700/50 transition-all duration-300">
      <div className="flex justify-between items-start mb-4">
        <div>
          <h3 className="text-2xl font-bold text-white mb-1">{game.name}</h3>
          <p className="text-slate-400 text-sm">{game.description}</p>
        </div>
        <div className={`flex flex-col items-center justify-center w-16 h-16 rounded-full border-2 ${getScoreColor(game.rokCodFeasibilityScore)} ${getScoreBg(game.rokCodFeasibilityScore)}`}>
          <span className="text-xl font-bold">{game.rokCodFeasibilityScore}</span>
          <span className="text-[10px] uppercase font-bold tracking-tighter">契合度</span>
        </div>
      </div>

      <div className="space-y-4">
        {/* Mechanics Section */}
        <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-700/50">
          <h4 className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-1">核心玩法循环</h4>
          <p className="text-slate-300 text-sm leading-relaxed">{game.gameplayMechanics}</p>
        </div>

        {/* Analysis Section */}
        <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-700/50">
          <h4 className="text-xs font-semibold text-purple-400 uppercase tracking-wider mb-1">ROK / COD 结合分析</h4>
          <p className="text-slate-300 text-sm leading-relaxed italic">"{game.rokCodAnalysis}"</p>
        </div>

        {/* Links */}
        <div className="flex gap-3 pt-2">
          <a 
            href={game.youtubeQuery} 
            target="_blank" 
            rel="noopener noreferrer"
            className="flex-1 bg-red-600 hover:bg-red-700 text-white text-center py-2 px-4 rounded-lg text-sm font-semibold transition-colors flex items-center justify-center gap-2"
          >
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M19.615 3.184c-3.604-.246-11.631-.245-15.23 0-3.897.266-4.356 2.62-4.385 8.816.029 6.185.484 8.549 4.385 8.816 3.6.245 11.626.246 15.23 0 3.897-.266 4.356-2.62 4.385-8.816-.029-6.185-.484-8.549-4.385-8.816zm-10.615 12.816v-8l8 3.993-8 4.007z"/></svg>
            观看视频
          </a>
          <a 
            href={game.googlePlayLink} 
            target="_blank" 
            rel="noopener noreferrer"
            className="flex-1 bg-green-600 hover:bg-green-700 text-white text-center py-2 px-4 rounded-lg text-sm font-semibold transition-colors flex items-center justify-center gap-2"
          >
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M3,20.5V3.5C3,2.91 3.34,2.39 3.84,2.15L13.69,12L3.84,21.85C3.34,21.6 3,21.09 3,20.5M16.81,15.12L6.05,25.88L20.06,11.91C20.71,11.55 20.71,10.45 20.06,10.09L6.05,3.92L16.81,14.68L17.5,15.12L16.81,15.12M14.5,13.5L5.5,4.5L5.5,19.5L14.5,10.5"/></svg>
            Play 商店
          </a>
        </div>
      </div>
    </div>
  );
};

export default GameCard;
