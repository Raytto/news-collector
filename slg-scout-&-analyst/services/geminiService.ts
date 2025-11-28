import { GoogleGenAI } from "@google/genai";
import { ReportData, RawGeminiResponse, GroundingSource } from "../types";
import { CORS_PROXY } from "./feishuService";

const apiKey = process.env.API_KEY;
const ai = new GoogleGenAI({ apiKey: apiKey });

const TARGET_CHANNELS = [
  { id: "UC_a0kACyJBpolI86S2b6HiQ", name: "Applovin (示例/待确认)" }, // 替换为真实频道名
  { id: "UC0nBV2jPf6n75nL2qSmvKfg", name: "Voodoo (示例/待确认)" },
  { id: "UCySabcVxWG9I18v0XI61MPg", name: "Playgendary (示例/待确认)" },
  { id: "UCCLVf7wyOpUmB63mVpJmU5w", name: "SayGames (示例/待确认)" }
];

interface RssVideoItem {
  title: string;
  link: string;
  published: Date;
  channelId: string;
}

// 通过 RSS 获取视频
async function fetchRSSUpdates(start: Date, end: Date): Promise<RssVideoItem[]> {
  const allVideos: RssVideoItem[] = [];

  for (const channel of TARGET_CHANNELS) {
    try {
      const rssUrl = `https://www.youtube.com/feeds/videos.xml?channel_id=${channel.id}`;
      // 使用代理获取 XML
      const response = await fetch(`${CORS_PROXY}${rssUrl}`);
      if (!response.ok) continue;
      
      const xmlText = await response.text();
      const parser = new DOMParser();
      const xmlDoc = parser.parseFromString(xmlText, "text/xml");
      const entries = xmlDoc.getElementsByTagName("entry");

      for (let i = 0; i < entries.length; i++) {
        const entry = entries[i];
        const publishedNode = entry.getElementsByTagName("published")[0];
        const titleNode = entry.getElementsByTagName("title")[0];
        const linkNode = entry.getElementsByTagName("link")[0];

        if (publishedNode && titleNode && linkNode) {
          const publishedTime = new Date(publishedNode.textContent || "");
          // Strict filtering
          if (publishedTime >= start && publishedTime <= end) {
            allVideos.push({
              title: titleNode.textContent || "Unknown Title",
              link: linkNode.getAttribute("href") || "",
              published: publishedTime,
              channelId: channel.id
            });
          }
        }
      }
    } catch (e) {
      console.warn(`Failed to fetch RSS for channel ${channel.id}:`, e);
    }
  }
  return allVideos;
}

export const generateMarketReport = async (): Promise<ReportData> => {
  const now = new Date();
  
  // Calculate Today 9:29 AM
  const endWindow = new Date(now);
  endWindow.setHours(9, 29, 0, 0);

  // Calculate Yesterday 9:31 AM
  const startWindow = new Date(now);
  startWindow.setDate(startWindow.getDate() - 1);
  startWindow.setHours(9, 31, 0, 0);

  const dateOptions: Intl.DateTimeFormatOptions = { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false };
  const timeWindowStr = `${startWindow.toLocaleDateString('zh-CN', dateOptions)} 至 ${endWindow.toLocaleDateString('zh-CN', dateOptions)}`;
  
  // 1. 尝试通过 RSS 获取精确数据
  let rssVideos: RssVideoItem[] = [];
  let useRssStrategy = false;
  
  try {
    rssVideos = await fetchRSSUpdates(startWindow, endWindow);
    if (rssVideos.length > 0) {
      useRssStrategy = true;
    }
  } catch (e) {
    console.error("RSS Fetch failed, falling back to search", e);
  }

  // 2. 构建 Prompt
  let prompt = "";
  let tools: any[] = [{ googleSearch: {} }];

  if (useRssStrategy) {
    // 策略 A: 已经有确切的视频列表，让 AI 分析这些视频
    // 此时不需要 Google Search 去找视频，但可能需要 Google Search 去找 Play Store 链接
    const videoListStr = rssVideos.map((v, i) => 
      `${i+1}. 标题: "${v.title}"\n   链接: ${v.link}\n   发布时间: ${v.published.toLocaleString('zh-CN')}`
    ).join('\n\n');

    prompt = `
      你现在是莉莉丝游戏（Lilith Games）的资深SLG市场分析师。
      
      ### 任务背景
      我已经通过技术手段抓取到了指定 YouTube 频道在 **${timeWindowStr}** 期间发布的新视频列表。
      
      ### 待分析视频清单
      ${videoListStr}

      ### 你的任务
      请针对上述每一个视频，执行以下操作：
      1. **游戏分析**：根据视频标题和已知信息（你可以使用 Google Search 搜索该游戏名称以获取更多详情），分析它的核心玩法。
      2. **副玩法评估**：评估该玩法是否适合作为《万国觉醒》(ROK) 或《万龙觉醒》(COD) 的副玩法 (Minigame)。评分标准：操作简单、反馈即时、适合买量。
      3. **Play Store 链接**：尝试搜索该游戏的 Google Play Store 链接。

      ### 输出格式 (JSON)
      {
        "games": [
          {
            "name": "游戏名称 (从标题提取)",
            "description": "简短描述（中文）",
            "mechanics": "核心玩法循环详解",
            "googlePlayLinkGuess": "Play Store 链接 (如果没有请留空)",
            "feasibilityScore": 8,
            "feasibilityAnalysis": "结合建议...",
            "originalYoutubeLink": "视频原始链接" 
          }
        ],
        "marketSummary": "总结今天在指定频道中发现了哪些值得关注的新品。"
      }
    `;
  } else {
    // 策略 B: RSS 没有数据（可能是没有新视频，或者 CORS 失败），回退到之前的模糊搜索
    const afterDate = startWindow.toISOString().split('T')[0];
    prompt = `
      你现在是莉莉丝游戏（Lilith Games）的资深SLG市场分析师。

      ### 核心任务
      我们需要监控 YouTube，找出在 **${timeWindowStr}** 这个时间窗口内上传的 Android 游戏 Gameplay 视频。
      由于 RSS 抓取未返回数据，请使用 Google Search 广泛搜索。

      ### 目标频道
      优先搜索这些 ID 的相关内容: ${TARGET_CHANNELS.map(c => c.id).join(', ')}。

      ### 搜索指令
      搜索 "New Android Games Soft Launch Gameplay after:${afterDate}" 以及指定频道的最新上传。

      ### 严格筛选
      必须确保视频发布时间在 ${timeWindowStr} 期间。

      ### 分析要求
      分析其作为《万国觉醒》(ROK) 或《万龙觉醒》(COD) 副玩法的可行性 (1-10分)。

      ### 输出格式 (JSON)
      请返回纯 JSON 格式：
      {
        "games": [
          {
            "name": "游戏名称",
            "description": "简短描述",
            "mechanics": "核心玩法",
            "googlePlayLinkGuess": "链接",
            "feasibilityScore": 8,
            "feasibilityAnalysis": "建议",
            "originalYoutubeLink": "" 
          }
        ],
        "marketSummary": "市场总结。"
      }
    `;
  }

  try {
    const response = await ai.models.generateContent({
      model: "gemini-2.5-flash", 
      contents: prompt,
      config: {
        tools: tools,
        temperature: 0.2,
      },
    });

    const text = response.text || "";
    
    // Extract Sources
    const sources: GroundingSource[] = [];
    const chunks = response.candidates?.[0]?.groundingMetadata?.groundingChunks || [];
    chunks.forEach((chunk: any) => {
      if (chunk.web?.uri && chunk.web?.title) {
        sources.push({ title: chunk.web.title, uri: chunk.web.uri });
      }
    });

    // Parse JSON
    const jsonMatch = text.match(/```json\n([\s\S]*?)\n```/) || text.match(/```([\s\S]*?)```/);
    let parsedData: RawGeminiResponse;

    if (jsonMatch && jsonMatch[1]) {
      parsedData = JSON.parse(jsonMatch[1]);
    } else {
      try {
        parsedData = JSON.parse(text);
      } catch (e) {
        console.error("JSON Parse Error:", text);
        parsedData = { games: [], marketSummary: "解析失败或未发现符合条件的游戏。" };
      }
    }

    // Map to internal type
    const processedGames = (parsedData.games || []).map((g: any) => ({
      name: g.name,
      youtubeQuery: g.originalYoutubeLink || `https://www.youtube.com/results?search_query=${encodeURIComponent(g.name + " gameplay")}`,
      googlePlayLink: (g.googlePlayLinkGuess && g.googlePlayLinkGuess.startsWith('http')) 
        ? g.googlePlayLinkGuess 
        : `https://play.google.com/store/search?q=${encodeURIComponent(g.name)}&c=apps`,
      description: g.description,
      gameplayMechanics: g.mechanics,
      rokCodFeasibilityScore: g.feasibilityScore,
      rokCodAnalysis: g.feasibilityAnalysis,
    }));

    return {
      date: timeWindowStr,
      games: processedGames,
      summary: useRssStrategy 
        ? `[RSS 精确抓取] ${parsedData.marketSummary}` 
        : `[AI 搜索模式] ${parsedData.marketSummary} (注: 未能通过 RSS 获取数据，可能是期间无更新或网络限制)`,
      sources: sources
    };

  } catch (error) {
    console.error("Error generating report:", error);
    throw error;
  }
};