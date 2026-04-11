/**
 * counsel.svelte.ts — State management for AI Counsel feature
 * Manages counsel selection, transient streaming state, session history,
 * per-member overrides, usage, and follow-ups.
 */

export interface CounselMember {
	model: string;
	role: string;
	system: string;
	ctx_tokens?: number;
	temperature?: number | null;
	max_tokens?: number | null;
}

export interface CounselChairperson {
	model: string;
	system: string;
	temperature?: number | null;
	max_tokens?: number | null;
}

export interface CounselConfig {
	name: string;
	description: string;
	chairperson: CounselChairperson;
	members: CounselMember[];
}

export interface MemberUsage {
	prompt: number;
	completion: number;
}

export interface MemberResponse {
	role: string;
	model: string;
	tokens: string[];
	done: boolean;
	error?: string;
	/** ms timestamp when the first token arrived; null until streaming starts */
	startedAt: number | null;
	/** ms timestamp when member_done / member_error received */
	finishedAt: number | null;
	/** From the `usage` SSE event for this role. */
	usage: MemberUsage | null;
}

/** Persisted deliberation data stored in message.extra */
export interface CounselDeliberation {
	type: 'counsel_deliberation';
	counselName: string;
	chairpersonModel: string;
	members: {
		role: string;
		model: string;
		content: string;
		error?: string;
	}[];
	usage?: {
		members: Record<string, MemberUsage>;
		chair: MemberUsage;
	};
	runId?: string;
	sessionId?: string;
}

export type CounselStatus = 'idle' | 'running_members' | 'running_synthesis' | 'done' | 'error';
export type LayoutMode = 'grid' | 'columns';

export interface MemberOverride {
	model?: string;
	system?: string;
	temperature?: number;
	max_tokens?: number;
}

export interface SessionInfo {
	id: string;
	title: string;
	created_at: number;
	updated_at: number;
	run_count: number;
}

export interface RunRecord {
	id: string;
	session_id: string;
	parent_run_id: string | null;
	task: string;
	counsel_snapshot: CounselConfig;
	synthesis: string;
	status: string;
	created_at: number;
	finished_at: number | null;
	members: {
		role: string;
		model: string;
		content: string;
		error?: string | null;
	}[];
	usage: {
		role: string;
		prompt_tokens: number;
		completion_tokens: number;
	}[];
}

const LAYOUT_STORAGE_KEY = 'counsel.layoutMode';

function loadLayoutMode(): LayoutMode {
	if (typeof localStorage === 'undefined') return 'grid';
	const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
	return raw === 'columns' ? 'columns' : 'grid';
}

class CounselStore {
	// Available counsel configs fetched from backend
	counsels = $state<CounselConfig[]>([]);
	counselsLoading = $state(false);

	// ── Chat-mode selection (replaces model selector) ───────────────────
	selectedForChat = $state<CounselConfig | null>(null);

	get isCounselMode(): boolean {
		return this.selectedForChat !== null;
	}

	selectForChat(counsel: CounselConfig) {
		this.selectedForChat = counsel;
	}

	clearChatSelection() {
		this.selectedForChat = null;
	}

	// ── Transient streaming state (live UI in chat messages) ────────────
	chatMemberResponses = $state<MemberResponse[]>([]);
	chatStatus = $state<CounselStatus>('idle');

	/** Reset transient chat streaming state */
	clearChatStreaming() {
		this.chatMemberResponses = [];
		this.chatStatus = 'idle';
	}

	// ── Standalone counsel page state ───────────────────────────────────
	selectedCounsel = $state<CounselConfig | null>(null);
	task = $state('');
	status = $state<CounselStatus>('idle');
	memberResponses = $state<MemberResponse[]>([]);
	synthesisTokens = $state<string[]>([]);
	error = $state<string | null>(null);
	autoSelectLoading = $state(false);

	// Layout toggle (persisted in localStorage)
	layoutMode = $state<LayoutMode>(loadLayoutMode());

	// Per-run member overrides keyed by role
	memberOverrides = $state<Record<string, MemberOverride>>({});

	// Usage aggregates
	chairUsage = $state<MemberUsage | null>(null);

	// Session + history state
	sessionId = $state<string | null>(null);
	lastRunId = $state<string | null>(null);
	sessions = $state<SessionInfo[]>([]);
	sessionRuns = $state<RunRecord[]>([]);
	viewingRunId = $state<string | null>(null); // when non-null, UI shows a read-only past run

	private abortController: AbortController | null = null;

	get synthesisText(): string {
		return this.synthesisTokens.join('');
	}

	get totalUsage(): MemberUsage {
		let prompt = 0;
		let completion = 0;
		for (const m of this.memberResponses) {
			if (m.usage) {
				prompt += m.usage.prompt;
				completion += m.usage.completion;
			}
		}
		if (this.chairUsage) {
			prompt += this.chairUsage.prompt;
			completion += this.chairUsage.completion;
		}
		return { prompt, completion };
	}

	getMemberText(role: string): string {
		const m = this.memberResponses.find((r) => r.role === role);
		return m ? m.tokens.join('') : '';
	}

	setLayoutMode(mode: LayoutMode) {
		this.layoutMode = mode;
		if (typeof localStorage !== 'undefined') {
			localStorage.setItem(LAYOUT_STORAGE_KEY, mode);
		}
	}

	setOverride(role: string, patch: MemberOverride | null) {
		if (patch === null) {
			delete this.memberOverrides[role];
			this.memberOverrides = { ...this.memberOverrides };
		} else {
			this.memberOverrides = {
				...this.memberOverrides,
				[role]: { ...this.memberOverrides[role], ...patch }
			};
		}
	}

	clearOverrides() {
		this.memberOverrides = {};
	}

	hasOverride(role: string): boolean {
		const o = this.memberOverrides[role];
		return !!o && Object.keys(o).some((k) => (o as Record<string, unknown>)[k] !== undefined);
	}

	async fetchCounsels() {
		if (this.counsels.length > 0 && !this.counselsLoading) return;
		this.counselsLoading = true;
		try {
			const res = await fetch('/api/counsels');
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			this.counsels = await res.json();
			if (!this.selectedCounsel && this.counsels.length > 0) {
				this.selectedCounsel = this.counsels[0];
			}
		} catch (e) {
			console.error('[counsel] failed to fetch counsels:', e);
		} finally {
			this.counselsLoading = false;
		}
	}

	async refreshCounsels() {
		this.counselsLoading = true;
		try {
			const res = await fetch('/api/counsels');
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			this.counsels = await res.json();
		} catch (e) {
			console.error('[counsel] failed to fetch counsels:', e);
		} finally {
			this.counselsLoading = false;
		}
	}

	async autoSelect(task: string) {
		if (!task.trim()) return;
		this.autoSelectLoading = true;
		try {
			const res = await fetch('/api/counsel/auto-select', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ task })
			});
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const data = await res.json();
			this.selectedCounsel = data.counsel;
		} catch (e) {
			console.error('[counsel] auto-select failed:', e);
		} finally {
			this.autoSelectLoading = false;
		}
	}

	// ── Sessions ────────────────────────────────────────────────────────

	async ensureSession(title: string = ''): Promise<string> {
		if (this.sessionId) return this.sessionId;
		try {
			const res = await fetch('/api/sessions', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ title })
			});
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const data = await res.json();
			this.sessionId = data.id;
			return data.id;
		} catch (e) {
			console.error('[counsel] ensureSession failed:', e);
			throw e;
		}
	}

	async fetchSessions() {
		try {
			const res = await fetch('/api/sessions');
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			this.sessions = await res.json();
		} catch (e) {
			console.error('[counsel] fetchSessions failed:', e);
		}
	}

	async loadSessionRuns(sessionId: string) {
		try {
			const res = await fetch(`/api/sessions/${sessionId}/runs`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			this.sessionRuns = await res.json();
			this.sessionId = sessionId;
		} catch (e) {
			console.error('[counsel] loadSessionRuns failed:', e);
		}
	}

	async viewRun(runId: string) {
		try {
			const res = await fetch(`/api/runs/${runId}`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const run: RunRecord = await res.json();
			this.viewingRunId = run.id;
			this.task = run.task;
			this.selectedCounsel = run.counsel_snapshot;
			const usageByRole = new Map(
				run.usage.map((u) => [
					u.role,
					{ prompt: u.prompt_tokens, completion: u.completion_tokens } satisfies MemberUsage
				])
			);
			this.memberResponses = run.members.map((m) => ({
				role: m.role,
				model: m.model,
				tokens: [m.content ?? ''],
				done: true,
				error: m.error ?? undefined,
				startedAt: null,
				finishedAt: null,
				usage: usageByRole.get(m.role) ?? null
			}));
			this.synthesisTokens = run.synthesis ? [run.synthesis] : [];
			this.chairUsage = usageByRole.get('__chair__') ?? null;
			this.status = run.status === 'completed' ? 'done' : 'error';
			this.lastRunId = run.id;
			this.sessionId = run.session_id;
		} catch (e) {
			console.error('[counsel] viewRun failed:', e);
		}
	}

	async deleteSession(sessionId: string) {
		try {
			const res = await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			if (this.sessionId === sessionId) {
				this.sessionId = null;
				this.sessionRuns = [];
			}
			this.sessions = this.sessions.filter((s) => s.id !== sessionId);
		} catch (e) {
			console.error('[counsel] deleteSession failed:', e);
		}
	}

	// ── Running a counsel ───────────────────────────────────────────────

	async run(task: string, counsel: CounselConfig, opts: { parentRunId?: string | null } = {}) {
		if (this.status === 'running_members' || this.status === 'running_synthesis') return;

		// Clear any read-only view we were showing.
		this.viewingRunId = null;

		this.task = task;
		this.status = 'running_members';
		this.error = null;
		this.synthesisTokens = [];
		this.chairUsage = null;
		this.memberResponses = counsel.members.map((m) => ({
			role: m.role,
			model: this.memberOverrides[m.role]?.model ?? m.model,
			tokens: [],
			done: false,
			startedAt: null,
			finishedAt: null,
			usage: null
		}));

		this.abortController = new AbortController();

		// Ensure a session exists before running.
		let sessionId: string | null = null;
		try {
			sessionId = await this.ensureSession(task.slice(0, 80));
		} catch {
			// Session creation failure shouldn't block the run — just skip persistence.
		}

		const parentRunId = opts.parentRunId ?? this.lastRunId;

		try {
			const res = await fetch('/api/counsel/run', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					task,
					counsel,
					session_id: sessionId,
					parent_run_id: parentRunId,
					member_overrides: Object.keys(this.memberOverrides).length
						? this.memberOverrides
						: undefined
				}),
				signal: this.abortController.signal
			});

			if (!res.ok) {
				const txt = await res.text();
				throw new Error(`HTTP ${res.status}: ${txt}`);
			}

			const reader = res.body!.getReader();
			const decoder = new TextDecoder();
			let buffer = '';

			while (true) {
				const { done, value } = await reader.read();
				if (done) break;
				buffer += decoder.decode(value, { stream: true });

				const lines = buffer.split('\n');
				buffer = lines.pop() ?? '';

				for (const line of lines) {
					if (!line.startsWith('data: ')) continue;
					const raw = line.slice(6).trim();
					if (!raw || raw === '[DONE]') continue;

					try {
						const event = JSON.parse(raw) as {
							type: string;
							role?: string;
							model?: string;
							token?: string;
							error?: string;
							run_id?: string;
							session_id?: string;
							prompt_tokens?: number;
							completion_tokens?: number;
						};

						switch (event.type) {
							case 'run_created': {
								if (event.run_id) this.lastRunId = event.run_id;
								if (event.session_id) this.sessionId = event.session_id;
								break;
							}
							case 'member_token': {
								const idx = this.memberResponses.findIndex((r) => r.role === event.role);
								if (idx !== -1) {
									const m = this.memberResponses[idx];
									if (m.startedAt === null) m.startedAt = Date.now();
									m.tokens.push(event.token ?? '');
								}
								break;
							}
							case 'member_done': {
								const idx = this.memberResponses.findIndex((r) => r.role === event.role);
								if (idx !== -1) {
									this.memberResponses[idx].done = true;
									this.memberResponses[idx].finishedAt = Date.now();
								}
								break;
							}
							case 'member_error': {
								const idx = this.memberResponses.findIndex((r) => r.role === event.role);
								if (idx !== -1) {
									this.memberResponses[idx].done = true;
									this.memberResponses[idx].finishedAt = Date.now();
									this.memberResponses[idx].error = event.error;
								}
								break;
							}
							case 'usage': {
								const usage: MemberUsage = {
									prompt: event.prompt_tokens ?? 0,
									completion: event.completion_tokens ?? 0
								};
								if (event.role === '__chair__') {
									this.chairUsage = usage;
								} else {
									const idx = this.memberResponses.findIndex((r) => r.role === event.role);
									if (idx !== -1) {
										this.memberResponses[idx].usage = usage;
									}
								}
								break;
							}
							case 'members_done': {
								this.status = 'running_synthesis';
								break;
							}
							case 'synthesis_token': {
								this.synthesisTokens.push(event.token ?? '');
								break;
							}
							case 'run_saved': {
								if (event.run_id) this.lastRunId = event.run_id;
								break;
							}
							case 'done': {
								this.status = 'done';
								break;
							}
							case 'cancelled': {
								this.status = 'idle';
								break;
							}
							case 'error': {
								this.error = event.error ?? 'Unknown error';
								this.status = 'error';
								break;
							}
						}
					} catch (parseErr) {
						console.warn('[counsel] SSE parse error:', parseErr, 'line:', line);
					}
				}
			}

			if (this.status !== 'error' && this.status !== 'idle') {
				this.status = 'done';
			}
		} catch (e: unknown) {
			if (e instanceof Error && e.name === 'AbortError') {
				this.status = 'idle';
			} else {
				this.error = e instanceof Error ? e.message : String(e);
				this.status = 'error';
			}
		} finally {
			this.abortController = null;
		}
	}

	stop() {
		if (this.abortController) {
			this.abortController.abort();
		}
		// The backend will finalize the run as cancelled and emit a cancelled event.
	}

	/** Submit a follow-up against the most recent run in this session. */
	async followUp(task: string) {
		if (!this.selectedCounsel || !this.lastRunId) return;
		await this.run(task, this.selectedCounsel, { parentRunId: this.lastRunId });
	}

	reset() {
		this.stop();
		this.status = 'idle';
		this.memberResponses = [];
		this.synthesisTokens = [];
		this.chairUsage = null;
		this.error = null;
		this.task = '';
		this.viewingRunId = null;
		this.lastRunId = null;
		this.sessionId = null;
		this.clearOverrides();
	}
}

export const counselStore = new CounselStore();
