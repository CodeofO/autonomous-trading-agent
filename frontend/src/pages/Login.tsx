import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { errorText, get, post } from "../api/client";
import { useAuth } from "../auth";
import { PaperBadge } from "../components/Layout";

export function Login() {
  const { login } = useAuth();
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: () => get<{ setup_required: boolean }>("/api/auth/status"),
  });
  const setup = status.data?.setup_required ?? false;
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    if (setup && password !== confirm) return setErr("비밀번호가 서로 다릅니다");
    if (password.length < 8) return setErr("비밀번호는 8자 이상이어야 합니다");
    setBusy(true);
    try {
      const r = await post<{ token: string }>(setup ? "/api/auth/setup" : "/api/auth/login", {
        username,
        password,
      });
      login(r.token);
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login card" onSubmit={submit}>
        <PaperBadge />
        <h1>🦉 AI 펀드매니저</h1>
        <p className="muted">
          {setup
            ? "처음 오셨네요! 이 컴퓨터에서만 쓰는 로컬 계정을 만들어 주세요. 비밀번호는 해시로만 저장돼요."
            : "로컬 계정으로 로그인하세요."}
        </p>
        <label>
          아이디
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>
          비밀번호
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={setup ? "new-password" : "current-password"}
          />
        </label>
        {setup && (
          <label>
            비밀번호 확인
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
            />
          </label>
        )}
        {err && <p className="error">{err}</p>}
        <button className="btn primary" disabled={busy || status.isLoading}>
          {setup ? "계정 만들고 시작하기" : "로그인"}
        </button>
      </form>
    </div>
  );
}
