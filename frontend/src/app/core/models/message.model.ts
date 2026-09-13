/** One turn in a Goal's chat thread (PA-02). */
export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
}
