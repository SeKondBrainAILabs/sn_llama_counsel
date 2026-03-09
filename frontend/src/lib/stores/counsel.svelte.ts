/**
 * counsel.svelte.ts — State management for AI Counsel feature
 * Mirrors the pattern from chat.svelte.ts using Svelte 5 runes.
 */

export interface CounselMember {
	model: string;
	role: string;
	system: string;
}

export interface CounselChairperson {
	model: string;
	system: string;
}

export interface CounselConfig {
	name: string;
	description: string;
	chairperson: CounselChairperson;
	members: CounselMember[];
}

export interface MemberResponse {
	role: string;
	model: string;
	tokens: string[];
	done: boolean;
	error?: string;
}

export type CounselStatus = 'idle' | 'running_members' | 'running_synthesis' | 'done' | 'error';

class CounselStore {
	// Available counsel configs fetched from backend
	counsels = $state<CounselConfig[]>([]);
	counselsLoading = $state(false);

	// Current session
	selectedCounsel = $state<CounselConfig | null>(null);
	task = $state('');
	status = $state<CounselStatus>('idle');
	memberResponses = $state<MemberResponse[]>([]);
	synthesisTokens = $state<string[]>([]);
	error = $state<string | null>(null);

	// Auto-select state
	autoSelectLoading = $state(false);

	private abortController: AbortController | null = null;

	get synthesisText(): string {
		return this.synthesisTokens.join('');
	}

	getMemberText(role: string): string {
		const m = this.memberResponses.find((r) => r.role === role);
		return m ? m.tokens.join('') : '';
	}

	async fetchCounsels() {
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

	async run(task: string, counsel: CounselConfig) {
		if (this.status === 'running_members' || this.status === 'running_synthesis') return;

		// Reset state
		this.task = task;
		this.status = 'running_members';
		this.error = null;
		this.synthesisTokens = [];
		this.memberResponses = counsel.members.map((m) => ({
			role: m.role,
			model: m.model,
			tokens: [],
			done: false
		}));

		this.abortController = new AbortController();

		try {
			const res = await fetch('/api/counsel/run', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ task, counsel }),
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
						};

						switch (event.type) {
							case 'member_token': {
								const idx = this.memberResponses.findIndex((r) => r.role === event.role);
								if (idx !== -1) {
									this.memberResponses[idx].tokens.push(event.token ?? '');
								}
								break;
							}
							case 'member_done': {
								const idx = this.memberResponses.findIndex((r) => r.role === event.role);
								if (idx !== -1) {
									this.memberResponses[idx].done = true;
								}
								break;
							}
							case 'member_error': {
								const idx = this.memberResponses.findIndex((r) => r.role === event.role);
								if (idx !== -1) {
									this.memberResponses[idx].done = true;
									this.memberResponses[idx].error = event.error;
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
							case 'done': {
								this.status = 'done';
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

			if (this.status !== 'error') {
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
		this.status = 'idle';
	}

	reset() {
		this.stop();
		this.status = 'idle';
		this.memberResponses = [];
		this.synthesisTokens = [];
		this.error = null;
		this.task = '';
	}
}

export const counselStore = new CounselStore();
