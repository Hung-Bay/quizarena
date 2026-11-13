import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../services/apiClient";
import {
  ShieldAlert,
  Users,
  BookOpen,
  Radio,
  Cpu,
  Search,
  CheckCircle,
  XCircle,
  AlertTriangle,
  History,
  EyeOff,
  RefreshCw,
} from "lucide-react";

interface AdminDashboardData {
  total_users: number;
  total_quizzes: number;
  total_games: number;
  active_games: number;
  active_ws_connections: number;
  ai_quota_used_today: number;
  ai_quota_remaining_today: number;
  ai_quota_limit: number;
  cached_at: string;
}

interface UserItem {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  roles: string[];
  created_at: string;
}

interface QuizItem {
  id: string;
  title: string;
  owner_id: string;
  visibility: string;
  status: string;
  is_hidden?: boolean;
  hidden_reason?: string;
  play_count: number;
  created_at: string;
}

interface AuditItem {
  id: number;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  metadata: Record<string, any> | null;
  created_at: string;
}

export const AdminPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"dashboard" | "users" | "quizzes" | "audit">("dashboard");
  const [userSearch, setUserSearch] = useState("");
  const [hideModalQuiz, setHideModalQuiz] = useState<QuizItem | null>(null);
  const [hideReason, setHideReason] = useState("");
  const [hideError, setHideError] = useState("");

  // 1. Dashboard Metrics Query
  const { data: metrics, isLoading: isMetricsLoading, refetch: refetchMetrics } = useQuery<AdminDashboardData>({
    queryKey: ["admin", "dashboard"],
    queryFn: () => apiRequest<AdminDashboardData>("/api/v1/admin/dashboard"),
    refetchInterval: 30000, // Cập nhật mỗi 30s
  });

  // 2. Users Query
  const { data: usersData } = useQuery<{ items: UserItem[]; total: number }>({
    queryKey: ["admin", "users", userSearch],
    queryFn: () =>
      apiRequest<{ items: UserItem[]; total: number }>(
        `/api/v1/admin/users?limit=50${userSearch ? `&search=${encodeURIComponent(userSearch)}` : ""}`
      ),
    enabled: activeTab === "users",
  });

  // 3. Quizzes Query
  const { data: publicQuizzes } = useQuery<QuizItem[]>({
    queryKey: ["admin", "quizzes"],
    queryFn: () => apiRequest<QuizItem[]>("/api/v1/quizzes/public"),
    enabled: activeTab === "quizzes",
  });

  // 4. Audit Logs Query
  const { data: auditData } = useQuery<{ items: AuditItem[]; total: number }>({
    queryKey: ["admin", "audit-logs"],
    queryFn: () => apiRequest<{ items: AuditItem[]; total: number }>("/api/v1/admin/audit-logs?limit=50"),
    enabled: activeTab === "audit",
  });

  // Mutations
  const toggleUserStatusMutation = useMutation({
    mutationFn: async ({ userId, isActive }: { userId: string; isActive: boolean }) => {
      return apiRequest(`/api/v1/admin/users/${userId}/status`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: isActive }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "dashboard"] });
    },
  });

  const toggleUserRoleMutation = useMutation({
    mutationFn: async ({ userId, role, action }: { userId: string; role: string; action: "grant" | "revoke" }) => {
      return apiRequest(`/api/v1/admin/users/${userId}/role`, {
        method: "PATCH",
        body: JSON.stringify({ role, action }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });

  const hideQuizMutation = useMutation({
    mutationFn: async ({ quizId, reason }: { quizId: string; reason: string }) => {
      return apiRequest(`/api/v1/admin/quizzes/${quizId}/hide`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
    },
    onSuccess: () => {
      setHideModalQuiz(null);
      setHideReason("");
      queryClient.invalidateQueries({ queryKey: ["admin", "quizzes"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "dashboard"] });
    },
  });

  const handleConfirmHide = (e: React.FormEvent) => {
    e.preventDefault();
    if (hideReason.trim().length < 3) {
      setHideError("Lý do phải có ít nhất 3 ký tự");
      return;
    }
    if (hideModalQuiz) {
      hideQuizMutation.mutate({ quizId: hideModalQuiz.id, reason: hideReason.trim() });
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-6 sm:p-10">
      <div className="max-w-7xl mx-auto space-y-8">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-6">
          <div>
            <div className="flex items-center gap-3">
              <span className="p-2.5 rounded-2xl bg-indigo-600/10 border border-indigo-500/20 text-indigo-400">
                <ShieldAlert className="w-6 h-6" />
              </span>
              <h1 className="text-3xl font-black text-white">Quản Trị Hệ Thống</h1>
            </div>
            <p className="text-slate-400 text-sm mt-1">
              Trung tâm điều hành và kiểm soát QuizArena (FR-ADM-01..05)
            </p>
          </div>

          <button
            onClick={() => refetchMetrics()}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900 border border-slate-800 hover:bg-slate-800 text-slate-300 text-sm font-semibold transition-colors cursor-pointer self-start sm:self-auto"
          >
            <RefreshCw className="w-4 h-4" />
            Làm mới số liệu
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex items-center gap-2 border-b border-slate-800 pb-px">
          {[
            { id: "dashboard", label: "Bảng Theo Dõi", icon: Radio },
            { id: "users", label: "Người Dùng", icon: Users },
            { id: "quizzes", label: "Kiểm Duyệt Quiz", icon: BookOpen },
            { id: "audit", label: "Nhật Ký Audit", icon: History },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`flex items-center gap-2 px-5 py-3 rounded-t-2xl font-bold text-sm transition-all cursor-pointer ${
                  isActive
                    ? "bg-slate-900 text-indigo-400 border-t border-x border-slate-800 border-b-2 border-b-indigo-500"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* TAB 1: DASHBOARD METRICS */}
        {activeTab === "dashboard" && (
          <div className="space-y-6">
            {isMetricsLoading ? (
              <div className="h-64 flex items-center justify-center text-slate-500">
                Đang tải số liệu hệ thống...
              </div>
            ) : metrics ? (
              <>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  {/* Active Games */}
                  <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 shadow-xl relative overflow-hidden">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-400">Trận Đang Chạy</span>
                    <div className="text-4xl font-black text-emerald-400 mt-2">{metrics.active_games}</div>
                    <p className="text-xs text-slate-500 mt-1">Tổng đã tổ chức: {metrics.total_games}</p>
                    <Radio className="absolute top-6 right-6 w-8 h-8 text-emerald-500/10" />
                  </div>

                  {/* Active WS Connections */}
                  <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 shadow-xl relative overflow-hidden">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-400">Kết Nối WebSocket</span>
                    <div className="text-4xl font-black text-indigo-400 mt-2">{metrics.active_ws_connections}</div>
                    <p className="text-xs text-slate-500 mt-1">Thời gian thực qua Nginx</p>
                    <Users className="absolute top-6 right-6 w-8 h-8 text-indigo-500/10" />
                  </div>

                  {/* Total Users */}
                  <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 shadow-xl relative overflow-hidden">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-400">Tổng Người Dùng</span>
                    <div className="text-4xl font-black text-white mt-2">{metrics.total_users}</div>
                    <p className="text-xs text-slate-500 mt-1">Host & Players đã đăng ký</p>
                    <Users className="absolute top-6 right-6 w-8 h-8 text-slate-500/10" />
                  </div>

                  {/* Total Quizzes */}
                  <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 shadow-xl relative overflow-hidden">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-400">Tổng Bộ Quiz</span>
                    <div className="text-4xl font-black text-amber-400 mt-2">{metrics.total_quizzes}</div>
                    <p className="text-xs text-slate-500 mt-1">Kho nội dung câu hỏi</p>
                    <BookOpen className="absolute top-6 right-6 w-8 h-8 text-amber-500/10" />
                  </div>
                </div>

                {/* Gemini AI Quota Card */}
                <div className="bg-slate-900 border border-slate-800 rounded-3xl p-8 shadow-xl">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
                    <div>
                      <h3 className="text-lg font-black text-white flex items-center gap-2">
                        <Cpu className="w-5 h-5 text-indigo-400" />
                        Quota Gemini API Hôm Nay (RPD)
                      </h3>
                      <p className="text-slate-400 text-xs mt-1">
                        Reset vào 00:00 múi giờ Pacific (America/Los_Angeles)
                      </p>
                    </div>

                    <div className="text-right">
                      <span className="text-2xl font-black text-indigo-300">
                        {metrics.ai_quota_used_today} / {metrics.ai_quota_limit}
                      </span>
                      <span className="text-xs text-slate-400 block">
                        Còn lại: {metrics.ai_quota_remaining_today} requests
                      </span>
                    </div>
                  </div>

                  {/* Quota Progress Bar */}
                  <div className="w-full bg-slate-800 rounded-full h-4 overflow-hidden p-0.5 border border-slate-700">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-indigo-400 transition-all duration-500"
                      style={{
                        width: `${Math.min(100, (metrics.ai_quota_used_today / metrics.ai_quota_limit) * 100)}%`,
                      }}
                    />
                  </div>
                </div>
              </>
            ) : null}
          </div>
        )}

        {/* TAB 2: USER MANAGEMENT */}
        {activeTab === "users" && (
          <div className="space-y-4">
            <div className="flex items-center gap-3 bg-slate-900 border border-slate-800 rounded-2xl px-4 py-2.5 max-w-md">
              <Search className="w-4 h-4 text-slate-400" />
              <input
                type="text"
                placeholder="Tìm email hoặc biệt danh..."
                value={userSearch}
                onChange={(e) => setUserSearch(e.target.value)}
                className="bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none w-full"
              />
            </div>

            <div className="bg-slate-900 border border-slate-800 rounded-3xl overflow-hidden shadow-xl">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-800/60 text-slate-400 font-bold uppercase text-xs">
                  <tr>
                    <th className="px-6 py-4">Người dùng</th>
                    <th className="px-6 py-4">Vai trò</th>
                    <th className="px-6 py-4">Trạng thái</th>
                    <th className="px-6 py-4 text-right">Thao tác</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {usersData?.items.map((u) => {
                    const isHost = u.roles.includes("HOST");
                    const isAdmin = u.roles.includes("ADMIN");
                    return (
                      <tr key={u.id} className="hover:bg-slate-800/30 transition-colors">
                        <td className="px-6 py-4">
                          <div className="font-bold text-white">{u.display_name}</div>
                          <div className="text-xs text-slate-400">{u.email}</div>
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex gap-1.5 flex-wrap">
                            {u.roles.map((r) => (
                              <span
                                key={r}
                                className={`text-[10px] font-black px-2 py-0.5 rounded-full ${
                                  r === "ADMIN"
                                    ? "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                                    : r === "HOST"
                                    ? "bg-indigo-500/10 text-indigo-400 border border-indigo-500/20"
                                    : "bg-slate-800 text-slate-300"
                                }`}
                              >
                                {r}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-6 py-4">
                          {u.is_active ? (
                            <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
                              <CheckCircle className="w-3.5 h-3.5" /> Hoạt động
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 text-xs text-rose-400">
                              <XCircle className="w-3.5 h-3.5" /> Đã khóa
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-4 text-right space-x-2">
                          {!isAdmin && (
                            <>
                              {/* Cấp/Thu hồi HOST */}
                              <button
                                onClick={() =>
                                  toggleUserRoleMutation.mutate({
                                    userId: u.id,
                                    role: "HOST",
                                    action: isHost ? "revoke" : "grant",
                                  })
                                }
                                className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all cursor-pointer ${
                                  isHost
                                    ? "bg-slate-800 hover:bg-slate-700 text-slate-300"
                                    : "bg-indigo-600 hover:bg-indigo-500 text-white"
                                }`}
                              >
                                {isHost ? "Thu quyền Host" : "Cấp quyền Host"}
                              </button>

                              {/* Khóa/Mở khóa */}
                              <button
                                onClick={() =>
                                  toggleUserStatusMutation.mutate({
                                    userId: u.id,
                                    isActive: !u.is_active,
                                  })
                                }
                                className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all cursor-pointer ${
                                  u.is_active
                                    ? "bg-rose-600/10 hover:bg-rose-600/20 text-rose-400 border border-rose-500/30"
                                    : "bg-emerald-600 hover:bg-emerald-500 text-white"
                                }`}
                              >
                                {u.is_active ? "Khóa" : "Mở khóa"}
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* TAB 3: QUIZ MODERATION */}
        {activeTab === "quizzes" && (
          <div className="space-y-4">
            <div className="bg-slate-900 border border-slate-800 rounded-3xl overflow-hidden shadow-xl">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-800/60 text-slate-400 font-bold uppercase text-xs">
                  <tr>
                    <th className="px-6 py-4">Tên Quiz</th>
                    <th className="px-6 py-4">Lượt chơi</th>
                    <th className="px-6 py-4">Trạng thái</th>
                    <th className="px-6 py-4 text-right">Kiểm duyệt</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {publicQuizzes?.map((q) => (
                    <tr key={q.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-6 py-4">
                        <div className="font-bold text-white">{q.title}</div>
                        <div className="text-xs text-slate-500">ID: {q.id}</div>
                      </td>
                      <td className="px-6 py-4 font-mono">{q.play_count}</td>
                      <td className="px-6 py-4">
                        <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                          Công khai
                        </span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <button
                          onClick={() => {
                            setHideModalQuiz(q);
                            setHideReason("");
                            setHideError("");
                          }}
                          className="px-3.5 py-1.5 rounded-xl bg-rose-600/10 hover:bg-rose-600/20 border border-rose-500/30 text-rose-400 text-xs font-bold transition-all cursor-pointer flex items-center gap-1.5 ml-auto"
                        >
                          <EyeOff className="w-3.5 h-3.5" />
                          Ẩn Quiz
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* TAB 4: AUDIT LOGS */}
        {activeTab === "audit" && (
          <div className="bg-slate-900 border border-slate-800 rounded-3xl overflow-hidden shadow-xl">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-800/60 text-slate-400 font-bold uppercase text-xs">
                <tr>
                  <th className="px-6 py-4">Thời gian</th>
                  <th className="px-6 py-4">Hành động</th>
                  <th className="px-6 py-4">Đối tượng</th>
                  <th className="px-6 py-4">Chi tiết</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800 font-mono text-xs">
                {auditData?.items.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="px-6 py-4 text-slate-400">
                      {new Date(log.created_at).toLocaleString("vi-VN")}
                    </td>
                    <td className="px-6 py-4">
                      <span className="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-bold">
                        {log.action}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-300">
                      {log.resource_type}: {log.resource_id.slice(0, 8)}...
                    </td>
                    <td className="px-6 py-4 text-slate-400 truncate max-w-xs">
                      {log.metadata ? JSON.stringify(log.metadata) : "--"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Modal Ẩn Quiz Bắt Buộc Lý Do (FR-ADM-03) */}
        {hideModalQuiz && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
            <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 max-w-md w-full shadow-2xl">
              <h3 className="text-xl font-black text-white mb-2 flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-rose-500" />
                Ẩn Quiz Công Khai
              </h3>
              <p className="text-slate-400 text-sm mb-4">
                Bạn đang ẩn quiz: <strong className="text-white">{hideModalQuiz.title}</strong>. Quiz sẽ bị gỡ khỏi danh sách công khai.
              </p>

              <form onSubmit={handleConfirmHide} className="space-y-4">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">
                    Lý do ẩn vi phạm (Bắt buộc)
                  </label>
                  <textarea
                    rows={3}
                    value={hideReason}
                    onChange={(e) => {
                      setHideReason(e.target.value);
                      setHideError("");
                    }}
                    placeholder="VD: Vi phạm bản quyền, ngôn từ không phù hợp..."
                    className="w-full px-4 py-3 rounded-2xl bg-slate-800 border border-slate-700 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500"
                    required
                  />
                  {hideError && <p className="text-rose-400 text-xs mt-1.5">{hideError}</p>}
                </div>

                <div className="flex items-center justify-end gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setHideModalQuiz(null)}
                    className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold text-sm cursor-pointer"
                  >
                    Hủy bỏ
                  </button>
                  <button
                    type="submit"
                    disabled={hideQuizMutation.isPending}
                    className="px-5 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-bold text-sm shadow-lg shadow-rose-900/30 cursor-pointer disabled:opacity-50"
                  >
                    {hideQuizMutation.isPending ? "Đang xử lý..." : "Xác nhận ẩn"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
