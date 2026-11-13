/**
 * Zod Schemas và TypeScript types cho WebSocket Protocol (v=1) theo docs/WS_PROTOCOL.md.
 *
 * "đừng tin dạng dữ liệu của server — dùng Zod để kiểm tra mọi thông điệp WS nhận về"
 */

import { z } from "zod";

// --- Option trong Question ---
export const WsOptionSchema = z.object({
  id: z.string(),
  content: z.string(),
  position: z.number().optional(),
});
export type WsOption = z.infer<typeof WsOptionSchema>;

// --- 1. lobby.update ---
export const LobbyUpdateDataSchema = z.object({
  game_id: z.string().optional(),
  pin: z.string().optional(),
  player_count: z.number(),
  players: z
    .array(
      z.object({
        id: z.string(),
        nickname: z.string(),
      })
    )
    .optional(),
  new_player: z
    .object({
      id: z.string(),
      nickname: z.string(),
    })
    .optional(),
  kicked_player_id: z.string().optional(),
});
export type LobbyUpdateData = z.infer<typeof LobbyUpdateDataSchema>;

// --- 2. game.starting ---
export const GameStartingDataSchema = z.object({
  countdown_s: z.number(),
  server_time: z.number(),
});
export type GameStartingData = z.infer<typeof GameStartingDataSchema>;

// --- 3. question.start ---
export const QuestionStartDataSchema = z.object({
  q_index: z.number(),
  total_questions: z.number(),
  question_id: z.string(),
  content: z.string(),
  type: z.string(),
  points: z.number(),
  time_limit_s: z.number(),
  server_time: z.number(),
  deadline: z.number(),
  options: z.array(WsOptionSchema),
});
export type QuestionStartData = z.infer<typeof QuestionStartDataSchema>;

// --- 4. answer.ack ---
export const AnswerAckDataSchema = z.object({
  accepted: z.boolean(),
  q_index: z.number(),
  reason: z.string().optional(),
});
export type AnswerAckData = z.infer<typeof AnswerAckDataSchema>;

// --- 5. question.result ---
export const PersonalResultSchema = z.object({
  score: z.number(),
  added_score: z.number(),
  streak: z.number(),
  rank: z.number(),
  is_correct: z.boolean(),
});
export type PersonalResult = z.infer<typeof PersonalResultSchema>;

export const PodiumEntrySchema = z.object({
  rank: z.number(),
  nickname: z.string(),
  score: z.number(),
});
export type PodiumEntry = z.infer<typeof PodiumEntrySchema>;

export const QuestionResultDataSchema = z.object({
  q_index: z.number(),
  correct_option_ids: z.array(z.string()),
  distribution: z.record(z.string(), z.number()),
  explanation: z.string().optional(),
  podium: z.array(PodiumEntrySchema).optional(),
  personal: PersonalResultSchema.optional(),
});
export type QuestionResultData = z.infer<typeof QuestionResultDataSchema>;

// --- 6. game.finished ---
export const GameFinishedDataSchema = z.object({
  game_id: z.string().optional(),
  podium: z.array(PodiumEntrySchema),
  total_players: z.number(),
  personal: z
    .object({
      score: z.number(),
      rank: z.number(),
    })
    .optional(),
});
export type GameFinishedData = z.infer<typeof GameFinishedDataSchema>;

// --- 7. player.kicked ---
export const PlayerKickedDataSchema = z.object({
  player_id: z.string(),
  reason: z.string().optional(),
});
export type PlayerKickedData = z.infer<typeof PlayerKickedDataSchema>;

// --- 8. snapshot ---
export const SnapshotDataSchema = z.object({
  status: z.enum([
    "lobby",
    "starting",
    "question_active",
    "question_result",
    "finished",
    "cancelled",
  ]),
  q_index: z.number().default(0),
  total_questions: z.number().default(0),
  is_host: z.boolean().default(false),
  pin: z.string().optional(),
  player_count: z.number().optional(),
  players: z
    .array(
      z.object({
        id: z.string(),
        nickname: z.string(),
        score: z.number().optional(),
      })
    )
    .optional(),
  current_question: QuestionStartDataSchema.optional(),
  player_score: z.number().optional(),
  player_streak: z.number().optional(),
  player_rank: z.number().optional(),
  answers_distribution: z.record(z.string(), z.number()).optional(),
  correct_option_ids: z.array(z.string()).optional(),
  explanation: z.string().optional(),
  podium: z.array(PodiumEntrySchema).optional(),
});
export type SnapshotData = z.infer<typeof SnapshotDataSchema>;

// --- 9. error & server.draining ---
export const ErrorDataSchema = z.object({
  code: z.string(),
  message: z.string(),
});
export type ErrorData = z.infer<typeof ErrorDataSchema>;

// --- Tổng hợp Inbound Envelope ---
export const InboundEnvelopeSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("lobby.update"), v: z.number(), data: LobbyUpdateDataSchema }),
  z.object({ type: z.literal("game.starting"), v: z.number(), data: GameStartingDataSchema }),
  z.object({ type: z.literal("question.start"), v: z.number(), data: QuestionStartDataSchema }),
  z.object({ type: z.literal("answer.ack"), v: z.number(), data: AnswerAckDataSchema }),
  z.object({ type: z.literal("question.result"), v: z.number(), data: QuestionResultDataSchema }),
  z.object({ type: z.literal("game.finished"), v: z.number(), data: GameFinishedDataSchema }),
  z.object({ type: z.literal("player.kicked"), v: z.number(), data: PlayerKickedDataSchema }),
  z.object({ type: z.literal("snapshot"), v: z.number(), data: SnapshotDataSchema }),
  z.object({ type: z.literal("error"), v: z.number(), data: ErrorDataSchema }),
  z.object({ type: z.literal("server.draining"), v: z.number(), data: z.any() }),
]);
export type InboundEnvelope = z.infer<typeof InboundEnvelopeSchema>;

// --- Helper tạo Outbound Message (Client -> Server) ---
export interface OutboundMessage {
  type:
    | "heartbeat"
    | "snapshot.request"
    | "game.start"
    | "game.next"
    | "game.end"
    | "player.kick"
    | "answer.submit";
  v: number;
  data: Record<string, unknown>;
}

export function buildHeartbeatMessage(): OutboundMessage {
  return { type: "heartbeat", v: 1, data: {} };
}

export function buildSnapshotRequest(): OutboundMessage {
  return { type: "snapshot.request", v: 1, data: {} };
}

export function buildGameStart(): OutboundMessage {
  return { type: "game.start", v: 1, data: {} };
}

export function buildGameNext(expectedQIndex?: number): OutboundMessage {
  return {
    type: "game.next",
    v: 1,
    data: expectedQIndex !== undefined ? { expected_q_index: expectedQIndex } : {},
  };
}

export function buildGameEnd(): OutboundMessage {
  return { type: "game.end", v: 1, data: {} };
}

export function buildPlayerKick(playerId: string): OutboundMessage {
  return { type: "player.kick", v: 1, data: { player_id: playerId } };
}

export function buildAnswerSubmit(
  qIndex: number,
  optionId: string,
  answerTimeMs: number
): OutboundMessage {
  return {
    type: "answer.submit",
    v: 1,
    data: {
      q_index: qIndex,
      option_id: optionId,
      answer_time_ms: answerTimeMs,
    },
  };
}
