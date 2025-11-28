export interface GameFinding {
  name: string;
  youtubeQuery: string;
  youtubeLink?: string;
  googlePlayLink?: string;
  description: string;
  gameplayMechanics: string;
  rokCodFeasibilityScore: number; // 1-10
  rokCodAnalysis: string;
}

export interface ReportData {
  date: string;
  games: GameFinding[];
  summary: string;
  sources: GroundingSource[];
}

export interface GroundingSource {
  title: string;
  uri: string;
}

// Helper type for parsing JSON from Gemini response
export interface RawGeminiResponse {
  games: {
    name: string;
    description: string;
    mechanics: string;
    googlePlayLinkGuess?: string; // AI might guess or find it
    feasibilityScore: number;
    feasibilityAnalysis: string;
  }[];
  marketSummary: string;
}