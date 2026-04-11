<script lang="ts">
	import { onMount } from 'svelte';
	import { counselStore } from '$lib/stores/counsel.svelte';

	let expanded = $state<string | null>(null);

	onMount(() => {
		counselStore.fetchSessions();
	});

	async function toggle(sessionId: string) {
		if (expanded === sessionId) {
			expanded = null;
			return;
		}
		expanded = sessionId;
		await counselStore.loadSessionRuns(sessionId);
	}

	function formatDate(ts: number): string {
		try {
			return new Date(ts).toLocaleString();
		} catch {
			return '';
		}
	}

	async function handleViewRun(runId: string) {
		await counselStore.viewRun(runId);
	}

	async function handleDelete(sessionId: string, e: MouseEvent) {
		e.stopPropagation();
		if (!confirm('Delete this session and all its runs?')) return;
		await counselStore.deleteSession(sessionId);
	}
</script>

<aside class="flex h-full w-72 shrink-0 flex-col border-r bg-muted/20">
	<div class="flex items-center justify-between border-b px-4 py-3">
		<h2 class="text-sm font-semibold">History</h2>
		<button
			class="rounded p-1 text-muted-foreground hover:bg-accent"
			onclick={() => counselStore.fetchSessions()}
			aria-label="Refresh history"
			title="Refresh"
		>
			↻
		</button>
	</div>

	<div class="flex-1 overflow-y-auto">
		{#if counselStore.sessions.length === 0}
			<p class="px-4 py-6 text-center text-xs text-muted-foreground italic">
				No past sessions yet.
			</p>
		{/if}

		{#each counselStore.sessions as session}
			<div class="border-b">
				<div
					class="flex w-full items-start justify-between gap-2 px-4 py-2 hover:bg-accent/40 {counselStore.sessionId ===
					session.id
						? 'bg-accent/30'
						: ''}"
				>
					<button
						class="min-w-0 flex-1 text-left"
						onclick={() => toggle(session.id)}
						aria-label="Toggle session"
					>
						<p class="truncate text-sm font-medium">
							{session.title || '(untitled)'}
						</p>
						<p class="text-[10px] text-muted-foreground">
							{session.run_count} run{session.run_count === 1 ? '' : 's'} ·
							{formatDate(session.updated_at)}
						</p>
					</button>
					<button
						class="shrink-0 rounded p-1 text-muted-foreground hover:text-destructive"
						onclick={(e) => handleDelete(session.id, e)}
						aria-label="Delete session"
						title="Delete"
					>
						✕
					</button>
				</div>

				{#if expanded === session.id}
					<div class="bg-background/60 px-4 py-2">
						{#each counselStore.sessionRuns as run}
							<button
								class="block w-full rounded px-2 py-1 text-left text-xs hover:bg-accent/40 {counselStore.viewingRunId ===
								run.id
									? 'bg-accent/50'
									: ''}"
								onclick={() => handleViewRun(run.id)}
							>
								<span class="truncate block font-medium">{run.task || '(no task)'}</span>
								<span class="text-[10px] text-muted-foreground">
									{run.status}
									{#if run.parent_run_id}· follow-up{/if}
								</span>
							</button>
						{/each}
					</div>
				{/if}
			</div>
		{/each}
	</div>

	<div class="border-t px-4 py-3">
		<button
			class="w-full rounded-md border px-3 py-1.5 text-xs font-medium transition-colors hover:bg-accent"
			onclick={() => counselStore.reset()}
		>
			＋ New Session
		</button>
	</div>
</aside>
