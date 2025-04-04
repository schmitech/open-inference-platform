export interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatStore {
  messages: Message[];
  voiceEnabled: boolean;
  isLoading: boolean;
  addMessage: (message: Message) => void;
  setVoiceEnabled: (enabled: boolean) => void;
  setIsLoading: (loading: boolean) => void;
  appendToLastMessage: (content: string) => void;
  clearMessages: () => void;
}

export interface StreamResponse {
  type: 'text' | 'audio';
  content: string;
  isFinal?: boolean;
}

export interface AppConfig {
  ollama: {
    base_url: string;
    temperature: number | string;
    top_p: number | string;
    top_k: number | string;
    repeat_penalty: number | string;
    num_predict: number | string;
    num_ctx: number | string;
    num_threads: number | string;
    model: string;
    embed_model: string;
    stream?: boolean | string;
  };
  vllm: {
    base_url: string;
    temperature: number | string;
    max_tokens: number | string;
    model: string;
    top_p: number | string;
    frequency_penalty: number | string;
    presence_penalty: number | string;
    best_of?: number | string;
    n?: number | string;
    logprobs?: number | string | null;
    echo?: boolean | string;
    stream?: boolean | string;
  };
  chroma: {
    host: string;
    port: number | string;
    collection: string;
    confidence_threshold: number | string;
  };
  eleven_labs: {
    api_key: string;
    voice_id: string;
  };
  general: {
    verbose: string;
    port: number | string;
    https?: {
      enabled: boolean;
      port: number;
      cert_file: string;
      key_file: string;
    };
  };
  elasticsearch: {
    enabled: boolean;
    node: string;
    index: string;
    auth: {
      username: string;
      password: string;
    }
  }
}