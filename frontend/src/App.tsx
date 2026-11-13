import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './context/AuthContext';
import { LoginPage } from './pages/LoginPage';
import { RegisterPage } from './pages/RegisterPage';
import { DashboardPage } from './pages/DashboardPage';
import { JoinPage } from './pages/JoinPage';
import { QuizListPage } from './pages/QuizListPage';
import { QuizEditorPage } from './pages/QuizEditorPage';
import { HostGamePage } from './pages/HostGamePage';
import { PlayerGamePage } from './pages/PlayerGamePage';
import { AdminPage } from './pages/AdminPage';
import { GameReportPage } from './pages/GameReportPage';
import { HistoryPage } from './pages/HistoryPage';
import { ProtectedRoute } from './components/ProtectedRoute';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/join" element={<JoinPage />} />

            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <DashboardPage />
                </ProtectedRoute>
              }
            />

            <Route
              path="/quizzes"
              element={
                <ProtectedRoute>
                  <QuizListPage />
                </ProtectedRoute>
              }
            />

            <Route
              path="/quizzes/new"
              element={
                <ProtectedRoute>
                  <QuizEditorPage />
                </ProtectedRoute>
              }
            />

            <Route
              path="/quizzes/:id/edit"
              element={
                <ProtectedRoute>
                  <QuizEditorPage />
                </ProtectedRoute>
              }
            />

            {/* Live Game Engine Routes */}
            <Route path="/play/:gameId" element={<PlayerGamePage />} />
            <Route path="/games/:gameId/play" element={<PlayerGamePage />} />

            {/* Post-game AI Report & Feedback */}
            <Route path="/games/:gameId/report" element={<GameReportPage />} />

            {/* Match History */}
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/me/games" element={<HistoryPage />} />

            <Route
              path="/host/:gameId"
              element={
                <ProtectedRoute>
                  <HostGamePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/games/:gameId/host"
              element={
                <ProtectedRoute>
                  <HostGamePage />
                </ProtectedRoute>
              }
            />

            {/* Admin Management & Dashboard */}
            <Route
              path="/admin"
              element={
                <ProtectedRoute>
                  <AdminPage />
                </ProtectedRoute>
              }
            />

            {/* Default fallback */}
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
};

export default App;
