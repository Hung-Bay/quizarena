/**
 * Hook useGameSocket: Quản lý kết nối WebSocket thời gian thực cho Live Game.
 *
 * Tuân thủ nghiêm ngặt các tiêu chí Phase 4:
 * 1. Ticket dùng một lần (TTL 30s) xin qua POST /api/v1/realtime/ws-ticket.
 * 2. Reconnect có backoff lũy tiến kèm jitter: min(1000 * 2^attempt, 10000) + rand(500).
 * 3. Xử lý mã đóng chuẩn: KICKED (4002) không nối lại; GAME_ENDED (4003) dừng; TICKET_INVALID (4001) xin vé mới.
 * 4. Xử lý sự kiện server.draining: kết nối lại tức thì.
 * 5. Xử lý visibilitychange và online khi điện thoại treo tab ở nền.
 * 6. React StrictMode resilience: cleanup đóng socket và hủy request ticket đang bay.
 * 7. Tích hợp MonotonicCountdown độc lập với đồng hồ máy khách.
 * 8. Gom nhịp cập nhật lobby (batching ~250ms) cho phòng đấu lớn.
 */

import { useEffect, useReducer, useRef, useCallback } from "react";
import { tokenStore } from "../services/tokenStore";
import { MonotonicCountdown } from "../services/monotonicClock";
import {
  InboundEnvelopeSchema,
  buildHeartbeatMessage,
  buildSnapshotRequest,
  buildGameStart,
  buildGameNext,
  buildGameEnd,
  buildPlayerKick,
  buildAnswerSubmit,
  QuestionStartData,
  QuestionResultData,
  GameFinishedData,
  PodiumEntry,
} from "../types/ws";

export type ConnectionStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "kicked"
  | "ended"
  | "error";

export interface PlayerInfo {
  id: string;
  nickname: string;
  score?: number;
}

export interface GameState {
  connectionStatus: ConnectionStatus;
  status: "lobby" | "starting" | "question_active" | "question_result" | "finished" | "cancelled";
  isHost: boolean;
  pin: string;
  playerCount: number;
  players: PlayerInfo[];
  currentQuestion: QuestionStartData | null;
  qIndex: number;
  totalQuestions: number;
  // Đồng hồ đếm ngược
  remainingSeconds: number;
  progressRatio: number;
  // Trạng thái nộp bài cá nhân
  selectedOptionId: string | null;
  isAnswerSubmitted: boolean;
  isAnswerAccepted: boolean | null;
  // Kết quả
  personalScore: number;
  personalStreak: number;
  personalRank: number;
  questionResult: QuestionResultData | null;
  gameFinished: GameFinishedData | null;
  podium: PodiumEntry[];
  errorMessage: string | null;
}

const initialState: GameState = {
  connectionStatus: "idle",
  status: "lobby",
  isHost: false,
  pin: "",
  playerCount: 0,
  players: [],
  currentQuestion: null,
  qIndex: 0,
  totalQuestions: 0,
  remainingSeconds: 0,
  progressRatio: 1.0,
  selectedOptionId: null,
  isAnswerSubmitted: false,
  isAnswerAccepted: null,
  personalScore: 0,
  personalStreak: 0,
  personalRank: 0,
  questionResult: null,
  gameFinished: null,
  podium: [],
  errorMessage: null,
};

type Action =
  | { type: "SET_CONNECTION_STATUS"; payload: ConnectionStatus }
  | { type: "SET_ERROR"; payload: string }
  | { type: "APPLY_SNAPSHOT"; payload: any }
  | { type: "LOBBY_UPDATE"; payload: { playerCount: number; players?: PlayerInfo[] } }
  | { type: "GAME_STARTING"; payload: { countdownS: number } }
  | { type: "QUESTION_START"; payload: QuestionStartData }
  | { type: "ANSWER_ACK"; payload: { accepted: boolean } }
  | { type: "SET_SELECTED_OPTION"; payload: string }
  | { type: "UPDATE_TIMER"; payload: { remainingSeconds: number; progressRatio: number } }
  | { type: "QUESTION_RESULT"; payload: QuestionResultData }
  | { type: "GAME_FINISHED"; payload: GameFinishedData }
  | { type: "PLAYER_KICKED" };

function gameReducer(state: GameState, action: Action): GameState {
  switch (action.type) {
    case "SET_CONNECTION_STATUS":
      return { ...state, connectionStatus: action.payload };

    case "SET_ERROR":
      return { ...state, errorMessage: action.payload };

    case "APPLY_SNAPSHOT": {
      const snap = action.payload;
      return {
        ...state,
        status: snap.status,
        qIndex: snap.q_index,
        totalQuestions: snap.total_questions,
        isHost: snap.is_host ?? state.isHost,
        pin: snap.pin ?? state.pin,
        playerCount: snap.player_count ?? (snap.players?.length || state.playerCount),
        players: snap.players ?? state.players,
        currentQuestion: snap.current_question ?? state.currentQuestion,
        personalScore: snap.player_score ?? state.personalScore,
        personalStreak: snap.player_streak ?? state.personalStreak,
        personalRank: snap.player_rank ?? state.personalRank,
        podium: snap.podium ?? state.podium,
        questionResult:
          snap.status === "question_result"
            ? {
                q_index: snap.q_index,
                correct_option_ids: snap.correct_option_ids ?? [],
                distribution: snap.answers_distribution ?? {},
                explanation: snap.explanation,
                podium: snap.podium,
              }
            : state.questionResult,
      };
    }

    case "LOBBY_UPDATE":
      return {
        ...state,
        playerCount: action.payload.playerCount,
        players: action.payload.players ?? state.players,
      };

    case "GAME_STARTING":
      return {
        ...state,
        status: "starting",
        remainingSeconds: action.payload.countdownS,
      };

    case "QUESTION_START":
      return {
        ...state,
        status: "question_active",
        currentQuestion: action.payload,
        qIndex: action.payload.q_index,
        totalQuestions: action.payload.total_questions,
        selectedOptionId: null,
        isAnswerSubmitted: false,
        isAnswerAccepted: null,
        questionResult: null,
        progressRatio: 1.0,
      };

    case "SET_SELECTED_OPTION":
      return {
        ...state,
        selectedOptionId: action.payload,
        isAnswerSubmitted: true,
      };

    case "ANSWER_ACK":
      return {
        ...state,
        isAnswerAccepted: action.payload.accepted,
      };

    case "UPDATE_TIMER":
      return {
        ...state,
        remainingSeconds: action.payload.remainingSeconds,
        progressRatio: action.payload.progressRatio,
      };

    case "QUESTION_RESULT":
      return {
        ...state,
        status: "question_result",
        questionResult: action.payload,
        personalScore: action.payload.personal?.score ?? state.personalScore,
        personalStreak: action.payload.personal?.streak ?? state.personalStreak,
        personalRank: action.payload.personal?.rank ?? state.personalRank,
        podium: action.payload.podium ?? state.podium,
      };

    case "GAME_FINISHED":
      return {
        ...state,
        status: "finished",
        gameFinished: action.payload,
        podium: action.payload.podium,
        personalScore: action.payload.personal?.score ?? state.personalScore,
        personalRank: action.payload.personal?.rank ?? state.personalRank,
      };

    case "PLAYER_KICKED":
      return {
        ...state,
        connectionStatus: "kicked",
        errorMessage: "Bạn đã bị Host loại khỏi phòng thi.",
      };

    default:
      return state;
  }
}

export function useGameSocket(gameId: string, customToken?: string | null) {
  const [state, dispatch] = useReducer(gameReducer, initialState);

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const heartbeatTimerRef = useRef<number | null>(null);
  const countdownRef = useRef<MonotonicCountdown | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef(true);

  // Batching buffer cho lobby updates (~250ms)
  const lobbyBufferRef = useRef<PlayerInfo[] | null>(null);
  const lobbyBatchTimerRef = useRef<number | null>(null);

  // Lấy token xác thực: customToken (guest token) hoặc in-memory access token
  const getAuthToken = useCallback(() => {
    if (customToken) return customToken;
    const guestStored = tokenStore.getGuestToken();
    if (guestStored) return guestStored;
    return tokenStore.getAccessToken();
  }, [customToken]);

  // Gửi thông điệp WS an toàn
  const sendMessage = useCallback((msg: object) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(msg));
    }
  }, []);

  // Xin ticket một lần
  const fetchTicket = useCallback(async (): Promise<string | null> => {
    const token = getAuthToken();
    if (!token) {
      dispatch({ type: "SET_ERROR", payload: "Chưa đăng nhập hoặc không có vé tham gia." });
      return null;
    }

    try {
      abortControllerRef.current = new AbortController();
      const res = await fetch("/api/v1/realtime/ws-ticket", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ game_id: gameId }),
        signal: abortControllerRef.current.signal,
      });

      if (!res.ok) {
        if (res.status === 403) {
          dispatch({ type: "SET_ERROR", payload: "Không có quyền truy cập trận đấu này." });
        }
        return null;
      }

      const data = await res.json();
      return data.ticket;
    } catch (e: any) {
      if (e.name === "AbortError") return null;
      return null;
    }
  }, [gameId, getAuthToken]);

  // Kết nối WebSocket
  const connect = useCallback(async () => {
    if (!isMountedRef.current) return;

    // Dọn dẹp kết nối cũ nếu có
    if (socketRef.current) {
      socketRef.current.onclose = null;
      socketRef.current.onerror = null;
      socketRef.current.close();
      socketRef.current = null;
    }

    dispatch({
      type: "SET_CONNECTION_STATUS",
      payload: reconnectAttemptRef.current > 0 ? "reconnecting" : "connecting",
    });

    const ticket = await fetchTicket();
    if (!ticket) {
      if (isMountedRef.current && reconnectAttemptRef.current < 5) {
        scheduleReconnect();
      }
      return;
    }

    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${wsProtocol}//${host}/ws/game?ticket=${ticket}`;

    const ws = new WebSocket(wsUrl);
    socketRef.current = ws;

    ws.onopen = () => {
      if (!isMountedRef.current) return;
      reconnectAttemptRef.current = 0;
      dispatch({ type: "SET_CONNECTION_STATUS", payload: "connected" });

      // Bật heartbeat định kỳ mỗi 15s
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = window.setInterval(() => {
        sendMessage(buildHeartbeatMessage());
      }, 15000);
    };

    ws.onmessage = (event) => {
      if (!isMountedRef.current) return;
      try {
        const rawJson = JSON.parse(event.data);
        const parsed = InboundEnvelopeSchema.safeParse(rawJson);
        if (!parsed.success) {
          console.warn("[WS] Thông điệp không khớp schema Zod:", rawJson, parsed.error);
          return;
        }

        const env = parsed.data;

        switch (env.type) {
          case "snapshot": {
            dispatch({ type: "APPLY_SNAPSHOT", payload: env.data });
            if (env.data.status === "question_active" && env.data.current_question) {
              startMonotonicTimer(
                env.data.current_question.deadline,
                env.data.current_question.server_time
              );
            }
            break;
          }

          case "lobby.update": {
            // Buffer updates để batch render 250ms
            if (env.data.players) {
              lobbyBufferRef.current = env.data.players;
            }
            if (!lobbyBatchTimerRef.current) {
              lobbyBatchTimerRef.current = window.setTimeout(() => {
                dispatch({
                  type: "LOBBY_UPDATE",
                  payload: {
                    playerCount: env.data.player_count,
                    players: lobbyBufferRef.current ?? undefined,
                  },
                });
                lobbyBatchTimerRef.current = null;
              }, 250);
            }
            break;
          }

          case "game.starting":
            dispatch({
              type: "GAME_STARTING",
              payload: { countdownS: env.data.countdown_s },
            });
            break;

          case "question.start":
            dispatch({ type: "QUESTION_START", payload: env.data });
            startMonotonicTimer(env.data.deadline, env.data.server_time);
            break;

          case "answer.ack":
            dispatch({ type: "ANSWER_ACK", payload: { accepted: env.data.accepted } });
            break;

          case "question.result":
            stopMonotonicTimer();
            dispatch({ type: "QUESTION_RESULT", payload: env.data });
            break;

          case "game.finished":
            stopMonotonicTimer();
            dispatch({ type: "GAME_FINISHED", payload: env.data });
            break;

          case "player.kicked":
            dispatch({ type: "PLAYER_KICKED" });
            ws.close(4002, "KICKED");
            break;

          case "server.draining":
            // Server báo replica đang drain -> kết nối lại tức thì
            console.warn("[WS] Server draining. Reconnecting immediately...");
            ws.close(1000, "Server draining");
            connect();
            break;

          case "error":
            dispatch({ type: "SET_ERROR", payload: env.data.message });
            break;
        }
      } catch (err) {
        console.error("[WS] Lỗi parse tin nhắn:", err);
      }
    };

    ws.onclose = (event) => {
      if (!isMountedRef.current) return;
      if (heartbeatTimerRef.current) {
        clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }
      stopMonotonicTimer();

      if (event.code === 4002) {
        // Bị kick -> không nối lại
        dispatch({ type: "PLAYER_KICKED" });
        return;
      }

      if (event.code === 4003) {
        // Trận đấu đã kết thúc
        dispatch({ type: "SET_CONNECTION_STATUS", payload: "ended" });
        return;
      }

      // Reconnect tự động
      scheduleReconnect();
    };

    ws.onerror = () => {
      // WebSocket error event will trigger onclose automatically
    };
  }, [fetchTicket, gameId, sendMessage]);

  // Lập lịch Reconnect có backoff lũy tiến kèm jitter
  const scheduleReconnect = useCallback(() => {
    if (!isMountedRef.current) return;
    const attempt = reconnectAttemptRef.current;
    // min(1000 * 2^attempt, 10000) + rand(500)
    const baseDelay = Math.min(1000 * Math.pow(2, attempt), 10000);
    const jitter = Math.random() * 500;
    const delay = baseDelay + jitter;

    reconnectAttemptRef.current += 1;
    dispatch({ type: "SET_CONNECTION_STATUS", payload: "reconnecting" });

    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = window.setTimeout(() => {
      connect();
    }, delay);
  }, [connect]);

  // Khởi động đồng hồ đếm ngược Monotonic
  const startMonotonicTimer = (deadline: number, serverTime: number) => {
    if (countdownRef.current) {
      countdownRef.current.stop();
    }

    countdownRef.current = new MonotonicCountdown(
      deadline,
      serverTime,
      (timerState) => {
        if (!isMountedRef.current) return;
        dispatch({
          type: "UPDATE_TIMER",
          payload: {
            remainingSeconds: timerState.remainingSeconds,
            progressRatio: timerState.progressRatio,
          },
        });
      }
    );
  };

  const stopMonotonicTimer = () => {
    if (countdownRef.current) {
      countdownRef.current.stop();
      countdownRef.current = null;
    }
  };

  // Các thao tác gửi lệnh của Host & Player
  const hostStartGame = useCallback(() => {
    sendMessage(buildGameStart());
  }, [sendMessage]);

  const hostNextQuestion = useCallback(
    (expectedIndex?: number) => {
      sendMessage(buildGameNext(expectedIndex));
    },
    [sendMessage]
  );

  const hostEndGame = useCallback(() => {
    sendMessage(buildGameEnd());
  }, [sendMessage]);

  const hostKickPlayer = useCallback(
    (playerId: string) => {
      sendMessage(buildPlayerKick(playerId));
    },
    [sendMessage]
  );

  const playerSubmitAnswer = useCallback(
    (optionId: string) => {
      if (state.isAnswerSubmitted || !state.currentQuestion) return;
      const answerTimeMs = countdownRef.current
        ? Math.round(
            (state.currentQuestion.time_limit_s * 1000) -
              countdownRef.current.getRemainingMs()
          )
        : 1000;

      dispatch({ type: "SET_SELECTED_OPTION", payload: optionId });
      sendMessage(buildAnswerSubmit(state.qIndex, optionId, Math.max(10, answerTimeMs)));

      // Rung phản hồi haptic trên điện thoại nếu được hỗ trợ
      if (typeof navigator !== "undefined" && "vibrate" in navigator) {
        navigator.vibrate(50);
      }
    },
    [sendMessage, state.currentQuestion, state.isAnswerSubmitted, state.qIndex]
  );

  // Vòng đời & React StrictMode cleanup
  useEffect(() => {
    isMountedRef.current = true;
    connect();

    // Lắng nghe visibilitychange và online
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
          reconnectAttemptRef.current = 0;
          connect();
        } else {
          sendMessage(buildSnapshotRequest());
        }
      }
    };

    const handleOnline = () => {
      if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
        reconnectAttemptRef.current = 0;
        connect();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("online", handleOnline);

    return () => {
      isMountedRef.current = false;
      if (abortControllerRef.current) abortControllerRef.current.abort();
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      if (lobbyBatchTimerRef.current) clearTimeout(lobbyBatchTimerRef.current);
      stopMonotonicTimer();

      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("online", handleOnline);

      if (socketRef.current) {
        socketRef.current.onclose = null;
        socketRef.current.onerror = null;
        socketRef.current.close(1000, "Component unmounted");
        socketRef.current = null;
      }
    };
  }, [connect, sendMessage]);

  return {
    state,
    hostStartGame,
    hostNextQuestion,
    hostEndGame,
    hostKickPlayer,
    playerSubmitAnswer,
  };
}
