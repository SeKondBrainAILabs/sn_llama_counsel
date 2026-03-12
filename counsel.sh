#!/bin/bash
# counsel.sh — Full-stack launcher for llama-counsel
#
# Starts ALL services from /Volumes/Studio Data Drive/llama/:
#   :11434  llama-server  (router — all models from models.ini)
#   :8080   mitmweb       (reverse proxy → :11434, inspect LLM calls)
#   :8081   mitmweb UI    (request/response inspector)
#   :5050   llama-counsel (⚖ AI Counsel + chat UI)
#
# Usage:
#   ./counsel.sh              Start everything (background, logs to ~/*)
#   ./counsel.sh stop         Stop everything (tree-kills model processes)
#   ./counsel.sh restart      Stop + start
#   ./counsel.sh status       Show what's running
#   ./counsel.sh fg           Run router in foreground with verbose logging
#   ./counsel.sh logs         Tail counsel log
#   ./counsel.sh logs router  Tail router log
#   ./counsel.sh logs mitmweb Tail mitmweb log

set -euo pipefail

# ── Paths (all inside this folder) ────────────────────────────────────────────

LLAMA_DIR="/Volumes/Studio Data Drive/llama"
BINARY="${LLAMA_DIR}/llama-server"
MODELS_INI="${LLAMA_DIR}/models.ini"
COUNSEL_DIR="${LLAMA_DIR}/sn_llama_counsel"
COUNSEL_VENV="${COUNSEL_DIR}/.venv/bin/llama-counsel"

# ── Ports ─────────────────────────────────────────────────────────────────────

ROUTER_PORT=11434
MITMWEB_PORT=8080
MITMWEB_UI_PORT=8081
COUNSEL_PORT=5050

# ── Logs ──────────────────────────────────────────────────────────────────────

ROUTER_LOG="$HOME/llama-router.log"
MITMWEB_LOG="$HOME/mitmweb.log"
COUNSEL_LOG="$HOME/llama-counsel.log"

# ── Helpers ───────────────────────────────────────────────────────────────────

port_pid() {
  lsof -iTCP:"$1" -sTCP:LISTEN -n -P 2>/dev/null | awk 'NR==2{print $2}'
}

port_in_use() {
  [ -n "$(port_pid "$1")" ]
}

green()  { printf "\033[32m%s\033[0m" "$1"; }
yellow() { printf "\033[33m%s\033[0m" "$1"; }
red()    { printf "\033[31m%s\033[0m" "$1"; }
bold()   { printf "\033[1m%s\033[0m" "$1"; }

wait_for_port() {
  local port=$1 name=$2 max=${3:-20}
  echo -n "  Waiting for $name"
  for i in $(seq 1 "$max"); do
    if port_in_use "$port"; then
      echo " $(green 'ready!')"
      return 0
    fi
    echo -n "."
    sleep 1
  done
  echo " $(red 'failed!')"
  return 1
}

stop_on_port() {
  local port=$1 name=$2
  if port_in_use "$port"; then
    local pid
    pid=$(port_pid "$port")
    echo "Stopping $name (PID $pid)..."
    kill "$pid" 2>/dev/null
    sleep 1
    # Force kill if still running
    if port_in_use "$port"; then
      kill -9 "$pid" 2>/dev/null
      sleep 1
    fi
  fi
}

# ── Status ────────────────────────────────────────────────────────────────────

show_status() {
  local lan_ip
  lan_ip=$(ifconfig en0 2>/dev/null | awk '/inet /{print $2}')

  echo ""
  echo "$(bold '⚖ llama-counsel full stack')"
  echo "─────────────────────────────────────────"

  if port_in_use $ROUTER_PORT; then
    echo "  Router    :$ROUTER_PORT  $(green '● running') (PID $(port_pid $ROUTER_PORT))"
  else
    echo "  Router    :$ROUTER_PORT  $(red '○ stopped')"
  fi

  if port_in_use $MITMWEB_PORT; then
    echo "  mitmweb   :$MITMWEB_PORT   $(green '● running') (PID $(port_pid $MITMWEB_PORT))"
  else
    echo "  mitmweb   :$MITMWEB_PORT   $(yellow '○ stopped')"
  fi

  if port_in_use $MITMWEB_UI_PORT; then
    echo "  Inspector :$MITMWEB_UI_PORT   $(green '● running')"
  else
    echo "  Inspector :$MITMWEB_UI_PORT   $(yellow '○ stopped')"
  fi

  if port_in_use $COUNSEL_PORT; then
    echo "  Counsel   :$COUNSEL_PORT   $(green '● running') (PID $(port_pid $COUNSEL_PORT))"
  else
    echo "  Counsel   :$COUNSEL_PORT   $(red '○ stopped')"
  fi

  echo "─────────────────────────────────────────"
  if port_in_use $COUNSEL_PORT; then
    echo ""
    echo "  Chat UI:     http://localhost:$COUNSEL_PORT"
    [ -n "$lan_ip" ] && echo "  Chat (LAN):  http://$lan_ip:$COUNSEL_PORT"
  fi
  if port_in_use $MITMWEB_UI_PORT; then
    echo "  Inspector:   http://localhost:$MITMWEB_UI_PORT"
  fi
  echo ""
}

# ── Stop ──────────────────────────────────────────────────────────────────────

do_stop() {
  echo "Stopping all services..."
  stop_on_port $COUNSEL_PORT "counsel"
  stop_on_port $MITMWEB_PORT "mitmweb"
  # Stop router last — kill entire process tree (children are model instances
  # that can use tens of GB each; orphaning them wastes all that RAM).
  if port_in_use $ROUTER_PORT; then
    local router_pid
    router_pid=$(port_pid $ROUTER_PORT)
    echo "Stopping router (PID $router_pid) and model processes..."
    pkill -P "$router_pid" 2>/dev/null || true
    sleep 1
    kill "$router_pid" 2>/dev/null || true
    sleep 1
    # Force kill anything remaining
    pkill -9 -P "$router_pid" 2>/dev/null || true
    kill -9 "$router_pid" 2>/dev/null || true
    sleep 1
  fi
  # Catch any orphaned model processes from previous failed stops
  pkill -f "llama-server.*--alias" 2>/dev/null || true
  sleep 1
  echo "Done."
}

# ── Start ─────────────────────────────────────────────────────────────────────

do_start() {
  # ── Preflight checks ──────────────────────────────────────────────────────

  if [ ! -x "$BINARY" ]; then
    echo "$(red 'Error:') llama-server not found at $BINARY"
    echo "  Fix: cp ~/llama.cpp/build/bin/llama-server \"$LLAMA_DIR/\""
    exit 1
  fi

  if [ ! -f "$MODELS_INI" ]; then
    echo "$(red 'Error:') models.ini not found at $MODELS_INI"
    exit 1
  fi

  if [ ! -x "$COUNSEL_VENV" ]; then
    echo "$(red 'Error:') llama-counsel not found at $COUNSEL_VENV"
    echo "  Fix: cd \"$COUNSEL_DIR\" && python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
  fi

  local lan_ip
  lan_ip=$(ifconfig en0 2>/dev/null | awk '/inet /{print $2}')

  echo ""
  echo "$(bold '════════════════════════════════════════════')"
  echo "  $(bold '⚖ Starting llama-counsel stack')"
  echo "$(bold '════════════════════════════════════════════')"
  echo ""

  # ── 1. llama-server (router) ──────────────────────────────────────────────

  if port_in_use $ROUTER_PORT; then
    echo "$(green '✓') Router already running on :$ROUTER_PORT"
  else
    echo "Starting llama-server router (:$ROUTER_PORT)..."
    nohup "$BINARY" \
      --models-preset "$MODELS_INI" \
      --host 0.0.0.0 \
      --port $ROUTER_PORT \
      --models-max 0 \
      --metrics \
      > "$ROUTER_LOG" 2>&1 &
    echo "  PID $! → $ROUTER_LOG"
    wait_for_port $ROUTER_PORT "router" 30 || {
      echo "  $(red 'Router failed to start.') Check: tail -20 $ROUTER_LOG"
      echo "  Continuing anyway (counsel UI will work without it)..."
    }
  fi

  # ── 2. mitmweb (reverse proxy + inspector) ────────────────────────────────

  if port_in_use $MITMWEB_PORT; then
    echo "$(green '✓') mitmweb already running on :$MITMWEB_PORT"
  else
    echo "Starting mitmweb (:$MITMWEB_PORT → :$ROUTER_PORT, UI :$MITMWEB_UI_PORT)..."
    if command -v mitmweb &>/dev/null; then
      nohup mitmweb \
        --mode "reverse:http://localhost:$ROUTER_PORT" \
        --listen-host 0.0.0.0 \
        --listen-port "$MITMWEB_PORT" \
        --web-host 0.0.0.0 \
        --web-port "$MITMWEB_UI_PORT" \
        --no-web-open-browser \
        > "$MITMWEB_LOG" 2>&1 &
      echo "  PID $! → $MITMWEB_LOG"
      wait_for_port $MITMWEB_PORT "mitmweb" 10 || true
    else
      echo "  $(yellow 'Warning:') mitmweb not found — skipping (brew install mitmproxy)"
    fi
  fi

  # ── 3. llama-counsel ──────────────────────────────────────────────────────

  if port_in_use $COUNSEL_PORT; then
    echo "$(green '✓') Counsel already running on :$COUNSEL_PORT"
  else
    echo "Starting llama-counsel (:$COUNSEL_PORT)..."
    nohup "$COUNSEL_VENV" \
      --host 0.0.0.0 \
      --port "$COUNSEL_PORT" \
      > "$COUNSEL_LOG" 2>&1 &
    echo "  PID $! → $COUNSEL_LOG"
    wait_for_port $COUNSEL_PORT "counsel" 15 || {
      echo "  $(red 'Counsel failed to start.') Check: tail -20 $COUNSEL_LOG"
      exit 1
    }
  fi

  # ── Done ──────────────────────────────────────────────────────────────────

  echo ""
  echo "$(bold '════════════════════════════════════════════')"
  echo "  $(bold '⚖ All services running')"
  echo "$(bold '════════════════════════════════════════════')"
  echo "  Chat UI:     http://localhost:$COUNSEL_PORT"
  [ -n "$lan_ip" ] && \
  echo "  Chat (LAN):  http://$lan_ip:$COUNSEL_PORT"
  echo "  Router API:  http://localhost:$ROUTER_PORT"
  echo "  Inspector:   http://localhost:$MITMWEB_UI_PORT"
  echo ""
  echo "  Logs:"
  echo "    Router:  tail -f $ROUTER_LOG"
  echo "    Counsel: tail -f $COUNSEL_LOG"
  echo "    mitmweb: tail -f $MITMWEB_LOG"
  echo "$(bold '════════════════════════════════════════════')"
  echo ""
}

# ── Logs ──────────────────────────────────────────────────────────────────────

do_logs() {
  case "${1:-counsel}" in
    counsel)  tail -f "$COUNSEL_LOG" ;;
    router)   tail -f "$ROUTER_LOG" ;;
    mitmweb)  tail -f "$MITMWEB_LOG" ;;
    *)
      echo "Usage: $0 logs {counsel|router|mitmweb}"
      exit 1
      ;;
  esac
}

# ── Foreground (verbose) ─────────────────────────────────────────────────────

do_foreground() {
  echo ""
  echo "$(bold '⚖ Router foreground mode (Ctrl+C to stop)')"
  echo "  All model loading, requests, and errors print to this terminal."
  echo ""
  exec "$BINARY" \
    --models-preset "$MODELS_INI" \
    --host 0.0.0.0 \
    --port $ROUTER_PORT \
    --models-max 0 \
    --metrics \
    --log-verbosity 1
}

# ── Main ──────────────────────────────────────────────────────────────────────

case "${1:-start}" in
  start)      do_start ;;
  stop)       do_stop ;;
  restart)    do_stop; sleep 2; do_start ;;
  status)     show_status ;;
  logs)       do_logs "${2:-counsel}" ;;
  fg|router)  do_foreground ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs [counsel|router|mitmweb]|fg}"
    exit 1
    ;;
esac
