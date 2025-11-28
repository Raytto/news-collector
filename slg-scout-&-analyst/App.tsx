import React, { useState } from 'react';
import { ReportData } from './types';
import { generateMarketReport } from './services/geminiService';
import { broadcastToFeishu } from './services/feishuService';
import GameCard from './components/GameCard';
import AnalysisHeader from './components/AnalysisHeader';
import SourceList from './components/SourceList';

const App: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<ReportData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pushStatus, setPushStatus] = useState<'idle' | 'sending' | 'success' | 'error'>('idle');
  const [pushLogs, setPushLogs] = useState<string[]>([]);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    setPushStatus('idle');
    setPushLogs([]);
    try {
      const data = await generateMarketReport();
      setReport(data);
    } catch (err: any) {
      setError(err.message || "生成报告时发生意外错误。");
    } finally {
      setLoading(false);
    }
  };

  const handlePushToFeishu = async () => {
    if (!report) return;

    setPushStatus('sending');
    setPushLogs(prev => [...prev, "开始请求飞书 Token (尝试使用 CORS Proxy)..."]);
    
    try {
        const results = await broadcastToFeishu(report);
        const successCount = results.filter(r => r.status === 'success').length;
        setPushLogs(prev => [...prev, ...results.map(r => `群组 [${r.name}]: ${r.status}`)]);
        
        if (successCount > 0) {
            setPushStatus('success');
            setPushLogs(prev => [...prev, `✅ 成功发送到 ${successCount} 个群组。`]);
        } else {
            setPushStatus('error');
            setPushLogs(prev => [...prev, `❌ 所有发送尝试均失败。`]);
        }
    } catch (e: any) {
        console.error(e);
        setPushStatus('error');
        setPushLogs(prev => [...prev, `❌ 发生错误: ${e.message}`]);
        alert(`推送流程中断。\n错误: ${e.message}\n\n重要提示: 这是一个纯前端应用。如果没有后端服务器，直接调用飞书 API 会被浏览器 CORS 拦截。本示例尝试使用公共 Proxy，但可能不稳定。`);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 font-sans text-slate-200 pb-20">
      {/* Navbar */}
      <nav className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="bg-blue-600 p-2 rounded-lg">
              <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
            </div>
            <span className="text-xl font-bold tracking-tight text-white">SLG 市场侦察兵</span>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-xs font-mono text-slate-500 border border-slate-800 rounded px-2 py-1">v1.4.0 RSS精准版</span>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        
        <div className="mb-4 text-center">
            <p className="text-xs text-yellow-500 bg-yellow-900/20 inline-block px-3 py-1 rounded border border-yellow-800/50">
                ⚠️ 使用前必须先 <a href="https://cors-anywhere.herokuapp.com/demo" target="_blank" className="underline font-bold hover:text-yellow-400">点击此处开启 CORS Proxy 权限</a>，否则无法抓取 RSS 和 推送飞书。
            </p>
        </div>

        {/* Initial Empty State / Call to Action */}
        {!report && !loading && !error && (
          <div className="flex flex-col items-center justify-center min-h-[50vh] text-center space-y-8">
            <div className="space-y-4 max-w-2xl">
              <h1 className="text-4xl md:text-5xl font-extrabold text-white">
                寻找下一个 <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-indigo-500">爆款副玩法</span>
              </h1>
              <p className="text-lg text-slate-400">
                基于 <b>YouTube RSS Feed</b> 精确扫描指定频道的最新软启动视频。<br/>
                发掘适合《万国觉醒》&《万龙觉醒》的 Minigame 创意。
              </p>
            </div>
            
            <button
              onClick={handleGenerate}
              className="group relative inline-flex items-center justify-center px-8 py-4 text-lg font-bold text-white transition-all duration-200 bg-blue-600 font-pj rounded-full focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-600 hover:bg-blue-500 active:scale-95"
            >
              <div className="absolute -inset-3 transition-all duration-1000 opacity-30 group-hover:opacity-100 group-hover:duration-200 animate-tilt">
                <div className="w-full h-full bg-gradient-to-r from-blue-400 to-indigo-500 rounded-full blur-lg"></div>
              </div>
              <span className="relative flex items-center gap-3">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                生成昨日情报
              </span>
            </button>
            <p className="text-xs text-slate-600 mt-4">检测窗口: 昨天 09:31 - 今天 09:29</p>
          </div>
        )}

        {/* Loading State */}
        {loading && (
          <div className="flex flex-col items-center justify-center min-h-[60vh]">
            <div className="relative">
              <div className="w-16 h-16 border-4 border-blue-200 border-dashed rounded-full animate-spin"></div>
              <div className="absolute top-0 left-0 w-16 h-16 border-4 border-blue-600 rounded-full animate-pulse opacity-50"></div>
            </div>
            <h2 className="mt-8 text-2xl font-semibold text-white">正在执行深度搜索...</h2>
            <div className="mt-2 flex flex-col items-center gap-1">
               <p className="text-slate-400 animate-pulse">正在解析 YouTube RSS Feeds...</p>
               <p className="text-slate-500 text-sm">正在验证视频上传时间...</p>
            </div>
          </div>
        )}

        {/* Error State */}
        {error && (
          <div className="rounded-xl border border-red-900/50 bg-red-900/20 p-8 text-center max-w-2xl mx-auto mt-10">
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-red-900/50 mb-4">
              <svg className="w-6 h-6 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            </div>
            <h3 className="text-xl font-bold text-red-200 mb-2">分析失败</h3>
            <p className="text-red-300/80 mb-6">{error}</p>
            <button
              onClick={handleGenerate}
              className="px-6 py-2 bg-red-800 hover:bg-red-700 text-white rounded-lg font-medium transition-colors"
            >
              重试
            </button>
          </div>
        )}

        {/* Results */}
        {report && !loading && (
          <div className="animate-fade-in-up">
            <div className="flex justify-between items-center mb-4">
                <AnalysisHeader data={report} />
                <div className="hidden lg:block ml-4">
                    <button 
                        onClick={handlePushToFeishu}
                        disabled={pushStatus === 'sending' || pushStatus === 'success'}
                        className={`flex flex-col items-center justify-center w-32 h-24 rounded-xl border transition-all ${
                            pushStatus === 'success' ? 'bg-green-600 border-green-500 text-white' :
                            pushStatus === 'error' ? 'bg-red-900/50 border-red-500 text-red-200' :
                            'bg-slate-800 border-slate-700 text-slate-400 hover:bg-slate-700 hover:text-white'
                        }`}
                    >
                        {pushStatus === 'sending' ? (
                            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-white mb-2"></div>
                        ) : pushStatus === 'success' ? (
                             <svg className="w-8 h-8 mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"/></svg>
                        ) : (
                            <svg className="w-8 h-8 mb-1" viewBox="0 0 24 24" fill="currentColor"><path d="M2,21L23,12L2,3V10L17,12L2,14V21Z" /></svg>
                        )}
                        <span className="text-xs font-bold text-center">
                            {pushStatus === 'success' ? '推送成功!' : '推送所有飞书群'}
                        </span>
                    </button>
                </div>
            </div>
            
            {/* Push Log Console */}
            {pushLogs.length > 0 && (
                <div className="mb-6 bg-black/50 p-4 rounded-lg font-mono text-xs text-green-400 border border-green-900/30">
                    <h4 className="text-slate-500 mb-2 border-b border-slate-700 pb-1">推送日志 (CORS Proxy 模式)</h4>
                    {pushLogs.map((log, i) => (
                        <div key={i}>{log}</div>
                    ))}
                    {pushStatus === 'error' && (
                        <div className="mt-2 text-yellow-500">
                             提示: 如果看到 "Failed to fetch"，请尝试访问 <a href="https://cors-anywhere.herokuapp.com/demo" target="_blank" className="underline">此链接</a> 申请临时访问权限。
                        </div>
                    )}
                </div>
            )}

            {/* Mobile Push Button */}
            <div className="lg:hidden mb-6">
                <button 
                    onClick={handlePushToFeishu}
                    disabled={pushStatus === 'sending' || pushStatus === 'success'}
                    className={`w-full py-3 rounded-lg flex items-center justify-center gap-2 font-bold ${
                        pushStatus === 'success' ? 'bg-green-600 text-white' :
                        'bg-blue-600 text-white'
                    }`}
                >
                    {pushStatus === 'sending' ? '推送中...' : '一键推送到飞书'}
                </button>
            </div>

            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-white flex items-center gap-2">
                <span className="w-2 h-8 bg-blue-500 rounded-full"></span>
                候选游戏名单
              </h2>
            </div>

            {report.games.length === 0 ? (
                <div className="text-center py-12 bg-slate-900/50 rounded-xl border border-dashed border-slate-700">
                    <p className="text-slate-400">昨日指定时间段内（09:31-09:29）指定频道未发布新视频。</p>
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {report.games.map((game, index) => (
                    <GameCard key={index} game={game} />
                ))}
                </div>
            )}

            <SourceList sources={report.sources} />
            
            <div className="mt-12 text-center">
                <button 
                  onClick={handleGenerate}
                  className="px-6 py-3 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 rounded-lg transition-colors text-sm font-medium"
                >
                    运行新扫描
                </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default App;